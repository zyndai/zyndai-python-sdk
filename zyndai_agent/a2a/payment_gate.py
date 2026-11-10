"""Inbound x402 payment gate for the A2A server (G19).

Mirrors `zyndai-ts-sdk/src/a2a/payment-gate.ts`.

x402 v2, `exact` scheme on an EVM network (default Base Sepolia test USDC).
Behaves like the Zynd n8n Gateway, so a Gateway listing and an SDK-hosted
agent look identical to a paying caller:

  1. no payment header       -> HTTP 402 + `PAYMENT-REQUIRED` (nothing consumed)
  2. payment header present  -> verify with the facilitator *before* the job runs
  3. job completed           -> settle on chain, return `PAYMENT-RESPONSE`
     job failed / cancelled  -> release; the caller is never charged
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union

from x402 import x402ResourceServerSync
from x402.http import HTTPFacilitatorClientSync
from x402.http.constants import DEFAULT_FACILITATOR_URL
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
)
from x402.mechanisms.evm.exact.register import register_exact_evm_server
from x402.schemas import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceConfig,
    ResourceInfo,
    SettleResponse,
)
from x402.schemas.errors import VerifyError

_log = logging.getLogger("zyndai.a2a.payment")

DEFAULT_X402_NETWORK = "eip155:84532"  # Base Sepolia
DEFAULT_X402_FACILITATOR_URL = DEFAULT_FACILITATOR_URL
# The signed authorization must outlive the job, because settlement happens after it.
DEFAULT_PAYMENT_TIMEOUT_SECONDS = 600

_SIGNATURE_HEADERS = ("payment-signature", "x-payment")
_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def parse_price_usd(price: Union[str, int, float, None]) -> Optional[float]:
    """`"$0.01"`, `"0.01 USDC"`, `0.01` -> a positive float, or None."""
    if price is None or isinstance(price, bool):
        return None
    if isinstance(price, (int, float)):
        n = float(price)
    else:
        # Anything before the number must be a currency marker ("$", "USD "), never a sign.
        m = re.match(r"^[^0-9.\-+]*(\d+(?:\.\d+)?|\.\d+)", str(price).strip())
        if not m:
            return None
        n = float(m.group(1))
    return n if n > 0 and n != float("inf") else None


def header_value(headers: Mapping[str, str], *names: str) -> Optional[str]:
    """Case-insensitive header lookup that works for Flask/Werkzeug and plain dicts."""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v:
            return v
    return None


@dataclass
class VerifiedPayment:
    payer: str
    payload: PaymentPayload
    requirements: PaymentRequirements
    #: EIP-3009 nonce, lower-cased. Stops one proof paying for two jobs.
    nonce: Optional[str]


@dataclass
class SettledPayment:
    success: bool
    transaction: str
    network: str
    payer: str
    #: Base64 value for the `PAYMENT-RESPONSE` header.
    header: str
    error_reason: Optional[str] = None


class PaymentRequiredError(Exception):
    """The caller must pay (or its payment was rejected). Renders as HTTP 402."""

    def __init__(self, message: str, challenge: PaymentRequired, reason: str) -> None:
        super().__init__(message)
        self.challenge = challenge
        #: Base64 value for the `PAYMENT-REQUIRED` header.
        self.header = encode_payment_required_header(challenge)
        self.reason = reason


class PaymentUnavailableError(Exception):
    """The facilitator could not be reached. Renders as HTTP 503: not the caller's fault."""


class X402PaymentGate:
    """Charges callers per task. Thread-safe; one instance per agent."""

    def __init__(
        self,
        *,
        price: Union[str, int, float],
        pay_to: str,
        network: str = DEFAULT_X402_NETWORK,
        facilitator_url: str = DEFAULT_X402_FACILITATOR_URL,
        max_timeout_seconds: int = DEFAULT_PAYMENT_TIMEOUT_SECONDS,
        description: Optional[str] = None,
        facilitator: Any = None,
    ) -> None:
        """
        Args:
            price: Price of one call, e.g. `"$0.01"`, `"0.01"` or `0.01` (USD). Must be > 0.
            pay_to: EVM address that receives the payment.
            network: CAIP-2 network id. Default `eip155:84532` (Base Sepolia).
            facilitator_url: x402 facilitator base URL.
            max_timeout_seconds: How long the signed authorization stays valid.
            description: Shown to the payer in the 402 challenge.
            facilitator: A sync facilitator client (tests, or one with auth headers).
                Overrides `facilitator_url`.
        """
        usd = parse_price_usd(price)
        if usd is None:
            raise ValueError(f"payment gate: price must be a positive amount, got {price!r}")
        if not _EVM_ADDRESS.match(pay_to or ""):
            raise ValueError(f"payment gate: pay_to is not an EVM address: {pay_to}")
        self.price_usd = usd
        self.pay_to = pay_to
        self.network = network
        self._timeout = int(max_timeout_seconds)
        self._description = description

        client = facilitator or HTTPFacilitatorClientSync(
            {"url": facilitator_url.rstrip("/")}
        )
        self._server = x402ResourceServerSync(client)
        register_exact_evm_server(self._server, networks=[network])

        self._lock = threading.Lock()
        self._initialized = False
        #: nonce -> expiry (epoch seconds). A proof can pay for one in-flight job only.
        self._claimed: dict[str, float] = {}

    # --- public ------------------------------------------------------------------------------

    def has_payment(self, headers: Mapping[str, str]) -> bool:
        """True when the request carries a payment proof at all."""
        return header_value(headers, *_SIGNATURE_HEADERS) is not None

    def challenge(
        self,
        resource_url: str,
        error: str = "Payment required",
        reason: str = "missing_payment",
    ) -> PaymentRequiredError:
        """Build a 402 for a caller that has not paid (or paid wrongly). Returned, not raised."""
        requirements = self._requirements()
        challenge = self._server.create_payment_required_response(
            requirements,
            ResourceInfo(
                url=resource_url,
                description=self._description,
                mime_type="application/json",
            ),
            error,
        )
        return PaymentRequiredError(error, challenge, reason)

    def verify(self, headers: Mapping[str, str], resource_url: str) -> VerifiedPayment:
        """Check a payment proof against this agent's price: structure, requirements match,
        in-process replay, then the facilitator's /verify. Raises PaymentRequiredError (the
        caller's problem) or PaymentUnavailableError (facilitator down).
        """
        raw = header_value(headers, *_SIGNATURE_HEADERS)
        if not raw:
            raise self.challenge(resource_url)

        try:
            payload = decode_payment_signature_header(raw)
        except Exception:
            raise self.challenge(resource_url, "Unreadable payment header", "malformed") from None
        if not isinstance(payload, PaymentPayload):
            raise self.challenge(
                resource_url, "Only x402 v2 payments are accepted", "unsupported_version"
            )

        required = self._requirements()[0]
        a = payload.accepted
        if not (
            a.scheme == required.scheme
            and a.network == required.network
            and str(a.asset).lower() == str(required.asset).lower()
            and str(a.pay_to).lower() == str(required.pay_to).lower()
            and str(a.amount) == str(required.amount)
        ):
            raise self.challenge(
                resource_url, "Payment does not match the requirements", "requirements_mismatch"
            )

        auth = (payload.payload or {}).get("authorization") or {}
        nonce = str(auth["nonce"]).lower() if auth.get("nonce") else None
        if nonce:
            try:
                expires_at = float(auth.get("validBefore") or 0) or time.time() + self._timeout
            except (TypeError, ValueError):
                expires_at = time.time() + self._timeout
            if not self._claim(nonce, expires_at):
                raise self.challenge(
                    resource_url, "Payment authorization was already used", "replayed"
                )

        try:
            res = self._server.verify_payment(payload, required)
        except VerifyError as e:
            self._unclaim(nonce)
            raise self.challenge(
                resource_url, "Payment rejected by the facilitator", e.invalid_reason or "invalid"
            ) from None
        except Exception as e:  # facilitator unreachable / unreadable
            self._unclaim(nonce)
            raise PaymentUnavailableError(f"Payment facilitator unavailable: {e}") from e

        if not res.is_valid:
            self._unclaim(nonce)
            raise self.challenge(
                resource_url,
                "Payment rejected by the facilitator",
                res.invalid_reason or "invalid",
            )
        return VerifiedPayment(
            payer=res.payer or str(auth.get("from") or ""),
            payload=payload,
            requirements=required,
            nonce=nonce,
        )

    def settle(self, v: VerifiedPayment) -> SettledPayment:
        """Submit the verified payment on chain. Call once, after the job completed."""
        try:
            res = self._server.settle_payment(v.payload, v.requirements)
        except Exception as e:
            self._unclaim(v.nonce)
            failed = SettleResponse(
                success=False,
                error_reason=getattr(e, "error_reason", None) or str(e) or "settle_failed",
                payer=v.payer,
                transaction=getattr(e, "transaction", None) or "",
                network=v.requirements.network,
            )
            return SettledPayment(
                success=False,
                transaction="",
                network=str(v.requirements.network),
                payer=v.payer,
                header=encode_payment_response_header(failed),
                error_reason=failed.error_reason,
            )
        if not res.success:
            self._unclaim(v.nonce)
        return SettledPayment(
            success=bool(res.success),
            transaction=res.transaction or "",
            network=str(res.network or v.requirements.network),
            payer=res.payer or v.payer,
            header=encode_payment_response_header(res),
            error_reason=res.error_reason,
        )

    def release(self, v: VerifiedPayment) -> None:
        """The job will not be charged (failed, cancelled, rejected): let the caller reuse it."""
        self._unclaim(v.nonce)

    # --- internals ---------------------------------------------------------------------------

    def _requirements(self) -> list[PaymentRequirements]:
        self._ensure_initialized()
        return self._server.build_payment_requirements(
            ResourceConfig(
                scheme="exact",
                pay_to=self.pay_to,
                price=f"${self.price_usd}",
                network=self.network,
                max_timeout_seconds=self._timeout,
            )
        )

    def _ensure_initialized(self) -> None:
        """Fetch the facilitator's /supported once. A failure is not cached, so a later call retries."""
        with self._lock:
            if self._initialized:
                return
            try:
                self._server.initialize()
            except Exception as e:
                raise PaymentUnavailableError(f"Payment facilitator unavailable: {e}") from e
            self._initialized = True

    def _claim(self, nonce: str, expires_at: float) -> bool:
        now = time.time()
        with self._lock:
            for n in [n for n, exp in self._claimed.items() if exp < now]:
                del self._claimed[n]
            if nonce in self._claimed:
                return False
            self._claimed[nonce] = expires_at
            return True

    def _unclaim(self, nonce: Optional[str]) -> None:
        if nonce:
            with self._lock:
                self._claimed.pop(nonce, None)

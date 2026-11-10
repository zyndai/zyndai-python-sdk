"""Outbound x402 payments for the A2A client (G19, client half).

Mirrors `zyndai-ts-sdk/src/a2a/payment-client.ts`.

When a call comes back HTTP 402, `A2APayer` reads the server's `PAYMENT-REQUIRED`
challenge, signs an EIP-3009 authorization with the caller's EVM key and returns the
`PAYMENT-SIGNATURE` header for one retry. Opt-in: an `A2AClient` built without
`payment` never signs anything.

Safety rails: a hard USD cap per call (default $0.10), and only assets whose decimals
are known are priced, so an unknown token cannot sneak past the cap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from x402 import x402ClientSync
from x402.http.x402_http_client import x402HTTPClientSync
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

from zyndai_agent.ed25519_identity import Ed25519Keypair

DEFAULT_MAX_PAYMENT_USD = 0.10

#: Assets priced 1:1 in USD, by lower-cased address -> decimals.
KNOWN_STABLECOINS: dict[str, int] = {
    "0x036cbd53842c5426634e7929541ec2318f3dcf7e": 6,  # USDC, Base Sepolia
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,  # USDC, Base
}


@dataclass
class ClientPaymentOptions:
    """How an `A2AClient` pays a 402."""

    #: Most the client will pay for one call, in USD.
    max_payment_usd: float = DEFAULT_MAX_PAYMENT_USD
    #: A `ClientEvmSigner` (e.g. `EthAccountSigner`). Default: the wallet of the client's
    #: Ed25519 identity, the same address `agent.pay_to_address` shows.
    signer: Any = None
    #: Extra stablecoins you trust to be worth $1, by address -> decimals.
    assets: dict[str, int] = field(default_factory=dict)


class PaymentRefusedError(Exception):
    """The server asked for more than the cap, or for something this client will not pay."""


class A2APayer:
    def __init__(self, keypair: Ed25519Keypair, options: Optional[ClientPaymentOptions] = None):
        opts = options or ClientPaymentOptions()
        self.max_payment_usd = float(opts.max_payment_usd)
        if not self.max_payment_usd > 0:
            raise ValueError("payment.max_payment_usd must be > 0")
        self._assets = dict(KNOWN_STABLECOINS)
        for addr, decimals in (opts.assets or {}).items():
            self._assets[addr.lower()] = int(decimals)

        signer = opts.signer
        if signer is None:
            # Imported here: zyndai_agent.payment configures logging on import.
            from zyndai_agent.payment import X402PaymentProcessor

            signer = EthAccountSigner(
                X402PaymentProcessor(
                    ed25519_private_key_bytes=keypair.private_key_bytes
                ).account
            )

        client = x402ClientSync()
        register_exact_evm_client(client, signer, policies=[self._within_cap])
        self._http = x402HTTPClientSync(client)

    # --- pricing -----------------------------------------------------------------------------

    def _usd(self, req: Any) -> Optional[float]:
        """USD value of a requirement, or None when the asset is not a trusted stablecoin."""
        decimals = self._assets.get(str(req.asset).lower())
        amount = str(req.get_amount())
        if decimals is None or not amount.isdigit():
            return None
        return int(amount) / 10**decimals

    def _within_cap(self, _version: int, reqs: list[Any]) -> list[Any]:
        out = []
        for r in reqs:
            usd = self._usd(r)
            if usd is not None and usd <= self.max_payment_usd:
                out.append(r)
        return out

    # --- the 402 ----------------------------------------------------------------------------

    def answer(self, headers: Mapping[str, str], body: Any) -> dict[str, str]:
        """Answer a 402. Returns the headers to send on the retry. Raises PaymentRefusedError
        when the challenge is unreadable, too expensive, or in a token/scheme this client
        cannot pay with.
        """
        # Our servers put the challenge in the PAYMENT-REQUIRED header; the JSON-RPC error
        # carries a copy, used when a proxy dropped the header.
        copy = None
        if isinstance(body, dict):
            copy = ((body.get("error") or {}).get("data") or {}).get("x402")
        body_bytes = json.dumps(copy if copy is not None else body).encode()
        try:
            out, _payload = self._http.handle_402_response(dict(headers), body_bytes)
            return dict(out)
        except Exception as e:
            asks = self._describe(headers, copy)
            raise PaymentRefusedError(
                f"Refusing to pay ({asks}; limit ${self.max_payment_usd}, "
                f"trusted stablecoins only): {e}"
            ) from e

    def _describe(self, headers: Mapping[str, str], copy: Any) -> str:
        try:
            get_header = {str(k).lower(): v for k, v in headers.items()}.get
            required = self._http.get_payment_required_response(
                lambda name: get_header(name.lower()), copy
            )
            parts = []
            for r in required.accepts:
                usd = self._usd(r)
                parts.append(f"${usd}" if usd is not None else f"{r.get_amount()} of {r.asset}")
            return " or ".join(parts) or "an unreadable amount"
        except Exception:
            return "an unreadable amount"

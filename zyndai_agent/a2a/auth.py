"""x-zynd-auth — per-message Ed25519 authorization for A2A traffic.

Mirrors `zyndai-ts-sdk/src/a2a/auth.ts` byte-for-byte on the signing
input, so a message signed by either SDK verifies on the other.

Signing rule (sender side):
  1. Build the Message exactly as it will go on the wire.
  2. Set `metadata["x-zynd-auth"]` with v, entity_id, public_key, nonce,
     issued_at, expires_at, fqan?, developer_proof? and `signature: ""`.
  3. Run JCS over the entire Message (parts + metadata + everything).
  4. Prepend `ZYND-A2A-MSG-v1\n` (domain separation tag).
  5. Sign with the agent's Ed25519 private key.
  6. Replace `signature: ""` with `ed25519:<base64-signature>`.

Verification rule (receiver side):
  1. Pull `auth = metadata["x-zynd-auth"]`. If absent, message is unsigned;
     handler-side policy decides whether to admit.
  2. Check version, expiry window, nonce-not-replayed.
  3. Hash `auth["public_key"]` and check the prefix matches `auth["entity_id"]`.
  4. Build the same byte string the sender signed: replace
     `signature` with `""`, JCS-canonicalize, prepend domain tag.
  5. Ed25519-verify with `auth["public_key"]`.
  6. (Optional) Verify `developer_proof` against the agent's pubkey.
"""

import base64
import copy
import hashlib
import os
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from zyndai_agent.a2a.canonical import canonical_bytes
from zyndai_agent.a2a.types import (
    ZYND_AUTH_DOMAIN_TAG,
    ZYND_AUTH_KEY,
    ZYND_AUTH_VERSION,
)
from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    sign as ed_sign,
    verify as ed_verify,
    verify_derivation_proof,
)


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------


_REASONS = (
    "missing_auth",
    "unsupported_version",
    "expired_or_skewed",
    "replay_detected",
    "entity_id_mismatch",
    "bad_signature",
    "bad_developer_proof",
    "untrusted_sender",
)


class ZyndAuthError(Exception):
    """Raised when an inbound message fails x-zynd-auth verification."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        if reason not in _REASONS:
            reason = "bad_signature"  # fall back rather than mask the real cause
        self.reason = reason


# -----------------------------------------------------------------------------
# Auth modes
# -----------------------------------------------------------------------------


AuthMode = Literal["strict", "permissive", "open"]
"""
strict      — reject inbound messages without valid x-zynd-auth.
permissive  — accept Zynd-signed messages (verified) AND unsigned messages.
              Handler can inspect `signed` to decide.
open        — accept everything; do not verify even if x-zynd-auth is present.
              Useful for protocol-conformance testing against vanilla A2A.
"""


# -----------------------------------------------------------------------------
# Replay cache
# -----------------------------------------------------------------------------


_DEFAULT_SKEW_SECONDS = 60
_MAX_NONCES_PER_SENDER = 4096


class ReplayCache:
    """In-process nonce cache for replay protection. Bounded per-sender.

    Each entry is (nonce, expires_at_epoch_ms). Sweeps on insert when a
    sender's bucket exceeds `_MAX_NONCES_PER_SENDER`.
    """

    def __init__(self) -> None:
        # Per-sender insertion-ordered nonce → exp_ms.
        self._per_sender: dict[str, OrderedDict[str, int]] = {}

    def check_and_record(
        self, entity_id: str, nonce: str, expires_at_epoch_ms: int
    ) -> bool:
        """Return True if this nonce was previously seen for this sender;
        record it (even on hit, refresh expiry) and return False otherwise.
        """
        now_ms = _now_epoch_ms()
        bucket = self._per_sender.setdefault(entity_id, OrderedDict())

        # Opportunistic GC + hard cap.
        if len(bucket) > _MAX_NONCES_PER_SENDER:
            for n, exp in list(bucket.items()):
                if exp < now_ms:
                    bucket.pop(n, None)
            while len(bucket) > _MAX_NONCES_PER_SENDER:
                bucket.popitem(last=False)

        existing = bucket.get(nonce)
        if existing is not None and existing >= now_ms:
            return True

        bucket[nonce] = expires_at_epoch_ms
        return False


# -----------------------------------------------------------------------------
# Sign
# -----------------------------------------------------------------------------


def sign_message(
    message: dict[str, Any],
    keypair: Ed25519Keypair,
    entity_id: str,
    *,
    fqan: Optional[str] = None,
    developer_proof: Optional[dict[str, Any]] = None,
    ttl_seconds: int = _DEFAULT_SKEW_SECONDS,
) -> dict[str, Any]:
    """Mutate `message` to add a fully-signed
    `metadata["x-zynd-auth"]` block. Returns the same `message` for
    fluency. `message` must be a plain dict (already serialized from a
    Pydantic model); we don't accept Message instances directly because
    the canonicalization layer wants a structurally-pure dict.
    """
    issued_at = datetime.now(tz=timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)

    auth: dict[str, Any] = {
        "v": ZYND_AUTH_VERSION,
        "entity_id": entity_id,
        "public_key": keypair.public_key_string,
        "nonce": _b64(os.urandom(16)),
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{issued_at.microsecond // 1000:03d}Z",
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{expires_at.microsecond // 1000:03d}Z",
        "signature": "",  # blanked for signing
    }
    if fqan:
        auth["fqan"] = fqan
    if developer_proof:
        auth["developer_proof"] = developer_proof

    message.setdefault("metadata", {})
    message["metadata"][ZYND_AUTH_KEY] = auth

    sig_input = _build_sig_input(message)
    signature = ed_sign(keypair.private_key, sig_input)
    auth["signature"] = signature
    return message


# -----------------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------------


class VerifyContext(dict):
    """Lightweight result type. Subclass of dict for ergonomic access:
    `ctx['signed']`, `ctx['entity_id']`, `ctx['fqan']`.
    """


def verify_message(
    message: dict[str, Any],
    *,
    mode: AuthMode = "permissive",
    replay_cache: Optional[ReplayCache] = None,
    skew_seconds: int = _DEFAULT_SKEW_SECONDS,
    verify_developer_proof: bool = True,
) -> VerifyContext:
    """Verify the `x-zynd-auth` block on a Message dict.

    Throws `ZyndAuthError` on any verification failure. Returns a
    VerifyContext describing whether the message was signed.

    mode:
      - strict:     requires a valid x-zynd-auth; raises missing_auth otherwise.
      - permissive: accepts a valid x-zynd-auth OR an unsigned message;
                    raises only when an x-zynd-auth is present but invalid.
      - open:       admits everything without verification.
    """
    metadata = message.get("metadata") or {}
    auth = metadata.get(ZYND_AUTH_KEY)

    if mode == "open":
        return VerifyContext(
            signed=False,
            entity_id=(auth or {}).get("entity_id"),
            fqan=(auth or {}).get("fqan"),
        )

    if not auth:
        if mode == "strict":
            raise ZyndAuthError(
                "missing_auth",
                "x-zynd-auth required (auth_mode=strict) but not present on inbound message",
            )
        return VerifyContext(signed=False, entity_id=None, fqan=None)

    # 1. Version
    if auth.get("v") != ZYND_AUTH_VERSION:
        raise ZyndAuthError(
            "unsupported_version",
            f"x-zynd-auth.v={auth.get('v')!r} is not supported (server expects v={ZYND_AUTH_VERSION})",
        )

    # 2. Expiry window
    try:
        issued_at_ms = _parse_iso_to_epoch_ms(auth["issued_at"])
        expires_at_ms = _parse_iso_to_epoch_ms(auth["expires_at"])
    except (KeyError, ValueError) as e:
        raise ZyndAuthError(
            "expired_or_skewed",
            f"x-zynd-auth.issued_at / expires_at are not parseable RFC 3339 timestamps: {e}",
        ) from e
    now_ms = _now_epoch_ms()
    skew_ms = skew_seconds * 1000
    if now_ms > expires_at_ms:
        raise ZyndAuthError(
            "expired_or_skewed",
            f"x-zynd-auth expired (now={now_ms}ms > expires_at={expires_at_ms}ms)",
        )
    if now_ms < issued_at_ms - skew_ms:
        raise ZyndAuthError(
            "expired_or_skewed",
            f"x-zynd-auth issued in the future beyond clock skew (issued_at={auth['issued_at']})",
        )

    # 3. Replay
    if replay_cache is not None:
        seen = replay_cache.check_and_record(
            auth["entity_id"], auth["nonce"], expires_at_ms
        )
        if seen:
            raise ZyndAuthError(
                "replay_detected",
                f"nonce {auth['nonce']} already seen for {auth['entity_id']}",
            )

    # 4. entity_id consistency
    expected = _entity_id_prefix(auth["public_key"])
    actual = _strip_entity_id_prefix(auth["entity_id"])
    if expected != actual:
        raise ZyndAuthError(
            "entity_id_mismatch",
            f"public_key hash ({expected}) does not match entity_id ({actual})",
        )

    # 5. Signature
    cloned = copy.deepcopy(message)
    cloned_auth = cloned["metadata"][ZYND_AUTH_KEY]
    cloned_auth["signature"] = ""
    sig_input = _build_sig_input(cloned)

    pub_b64 = auth["public_key"]
    if pub_b64.startswith("ed25519:"):
        pub_b64 = pub_b64[len("ed25519:"):]
    if not ed_verify(pub_b64, sig_input, auth["signature"]):
        raise ZyndAuthError("bad_signature", "x-zynd-auth signature did not verify")

    # 6. Developer proof (optional)
    if verify_developer_proof and "developer_proof" in auth:
        proof = auth["developer_proof"]
        if not verify_derivation_proof(proof, pub_b64):
            raise ZyndAuthError(
                "bad_developer_proof",
                "x-zynd-auth.developer_proof did not verify against this agent's public key",
            )

    return VerifyContext(
        signed=True,
        entity_id=auth["entity_id"],
        fqan=auth.get("fqan"),
    )


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _build_sig_input(message: dict[str, Any]) -> bytes:
    canon = canonical_bytes(message)
    tag = ZYND_AUTH_DOMAIN_TAG.encode("utf-8")
    return tag + canon


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _parse_iso_to_epoch_ms(s: str) -> int:
    # Accept "...Z" and "...+00:00" forms. fromisoformat in 3.11+ handles
    # both; older versions need the Z-stripping fallback.
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except ValueError as e:
        raise ValueError(f"unparseable timestamp {s!r}") from e


def _entity_id_prefix(public_key_string: str) -> str:
    b64 = public_key_string
    if b64.startswith("ed25519:"):
        b64 = b64[len("ed25519:"):]
    raw = base64.b64decode(b64)
    digest = hashlib.sha256(raw).digest()
    return digest[:16].hex()


def _strip_entity_id_prefix(entity_id: str) -> str:
    # entity_id formats:
    #   zns:<32-hex>           (agent)
    #   zns:svc:<32-hex>       (service)
    if entity_id.startswith("zns:svc:"):
        return entity_id[len("zns:svc:"):]
    if entity_id.startswith("zns:"):
        return entity_id[len("zns:"):]
    return entity_id

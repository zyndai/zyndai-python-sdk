"""
Message signing and verification for the typed message protocol.

Wraps the existing Ed25519 sign/verify from ed25519_identity.py to work
with Pydantic-based TypedMessage models. Uses canonical JSON serialization
(sorted keys, deterministic datetime encoding) for signature stability.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from zyndai_agent.ed25519_identity import sign as ed25519_sign, verify as ed25519_verify

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from zyndai_agent.typed_messages import MessageBase


def _canonical_bytes(message: MessageBase) -> bytes:
    """
    Produce deterministic bytes from a typed message for signing/verification.
    Excludes the 'signature' field so the signature doesn't cover itself.
    Uses mode="json" so datetimes are ISO strings, not Python repr.
    """
    data = message.model_dump(mode="json", exclude={"signature"})
    return json.dumps(data, sort_keys=True).encode("utf-8")


def sign_message(message: MessageBase, private_key: Ed25519PrivateKey) -> str:
    """
    Sign a typed message with an Ed25519 private key.

    Returns signature in 'ed25519:<b64>' format matching the existing
    ed25519_identity convention.
    """
    payload = _canonical_bytes(message)
    return ed25519_sign(private_key, payload)


def verify_message(message: MessageBase, public_key_b64: str) -> bool:
    """
    Verify the Ed25519 signature on a typed message.

    Args:
        message: The typed message with a populated 'signature' field.
        public_key_b64: Base64-encoded 32-byte Ed25519 public key
                        (without the 'ed25519:' prefix).

    Returns True if valid, False otherwise. Never raises.
    """
    payload = _canonical_bytes(message)
    return ed25519_verify(public_key_b64, payload, message.signature)

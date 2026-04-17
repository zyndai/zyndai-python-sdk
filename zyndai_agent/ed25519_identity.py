"""
Ed25519 Identity Module for agent-dns decentralized registry.

Provides Ed25519 keypair generation, signing, verification, and HD derivation
compatible with the Go agent-dns implementation.
"""

import base64
import hashlib
import json
import struct
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization


class Ed25519Keypair:
    """Holds an Ed25519 keypair with convenience methods."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private_key = private_key
        self._public_key = private_key.public_key()

    @property
    def private_key(self) -> Ed25519PrivateKey:
        return self._private_key

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    @property
    def private_key_bytes(self) -> bytes:
        """Raw 32-byte private key seed."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def public_key_bytes(self) -> bytes:
        """Raw 32-byte public key."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def private_key_b64(self) -> str:
        """Base64-encoded private key seed."""
        return base64.b64encode(self.private_key_bytes).decode()

    @property
    def public_key_b64(self) -> str:
        """Base64-encoded public key."""
        return base64.b64encode(self.public_key_bytes).decode()

    @property
    def public_key_string(self) -> str:
        """Public key in 'ed25519:<b64>' format."""
        return f"ed25519:{self.public_key_b64}"

    @property
    def entity_id(self) -> str:
        """Derive the agent-flavor entity ID (zns:<hash>) from this keypair's public key."""
        return generate_entity_id(self.public_key_bytes, "agent")


def generate_keypair() -> Ed25519Keypair:
    """Generate a new Ed25519 keypair."""
    private_key = Ed25519PrivateKey.generate()
    return Ed25519Keypair(private_key)


def keypair_from_private_bytes(private_bytes: bytes) -> Ed25519Keypair:
    """Create keypair from raw 32-byte private key seed."""
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    return Ed25519Keypair(private_key)


def load_keypair(path: str) -> Ed25519Keypair:
    """
    Load keypair from JSON file.
    Format: {"public_key": "<b64>", "private_key": "<b64>"}
    """
    with open(path, "r") as f:
        data = json.load(f)
    private_bytes = base64.b64decode(data["private_key"])
    return keypair_from_private_bytes(private_bytes)


def load_keypair_with_metadata(path: str) -> tuple:
    """
    Load keypair and derivation metadata from JSON file.

    Returns:
        tuple: (Ed25519Keypair, dict or None) — keypair and derived_from metadata if present
    """
    with open(path, "r") as f:
        data = json.load(f)
    private_bytes = base64.b64decode(data["private_key"])
    kp = keypair_from_private_bytes(private_bytes)
    derivation_metadata = data.get("derived_from")
    return kp, derivation_metadata


def save_keypair(kp: Ed25519Keypair, path: str, derivation_metadata: Optional[dict] = None) -> None:
    """
    Save keypair to JSON file.
    Format: {"public_key": "<b64>", "private_key": "<b64>"}
    Optionally includes derivation metadata under "derived_from".
    """
    data = {
        "public_key": kp.public_key_b64,
        "private_key": kp.private_key_b64,
    }
    if derivation_metadata:
        data["derived_from"] = derivation_metadata
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def generate_entity_id(public_key_bytes: bytes, entity_type: str = "agent") -> str:
    """
    Derive an entity_id from Ed25519 public key bytes.

    entity_type="agent"   → "zns:<hex>"        (agent-flavor)
    entity_type="service" → "zns:svc:<hex>"    (service-flavor)
    Anything else falls back to agent-flavor. Matches Go
    models.GenerateEntityID byte-for-byte.
    """
    digest = hashlib.sha256(public_key_bytes).digest()
    suffix = digest[:16].hex()
    if entity_type == "service":
        return "zns:svc:" + suffix
    return "zns:" + suffix


def generate_developer_id(public_key_bytes: bytes) -> str:
    """
    Generate developer ID from public key bytes.
    Format: zns:dev:<sha256(pub)[:16].hex()>
    """
    digest = hashlib.sha256(public_key_bytes).digest()
    return "zns:dev:" + digest[:16].hex()


def sign(private_key: Ed25519PrivateKey, message: bytes) -> str:
    """
    Sign a message and return 'ed25519:<b64 signature>'.
    Matches identity.go:95-98.
    """
    sig_bytes = private_key.sign(message)
    return "ed25519:" + base64.b64encode(sig_bytes).decode()


def verify(public_key_b64: str, message: bytes, signature: str) -> bool:
    """
    Verify an Ed25519 signature.
    Matches identity.go:102-118.

    Args:
        public_key_b64: Base64-encoded 32-byte public key
        message: The original message bytes
        signature: Signature in 'ed25519:<b64>' format

    Returns:
        True if valid, False otherwise
    """
    try:
        pub_bytes = base64.b64decode(public_key_b64)
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

        if not signature.startswith("ed25519:"):
            return False
        sig_b64 = signature[len("ed25519:"):]
        sig_bytes = base64.b64decode(sig_b64)

        pub_key.verify(sig_bytes, message)
        return True
    except Exception:
        return False


def public_key_string(pub_b64: str) -> str:
    """Format public key as 'ed25519:<b64>'."""
    return f"ed25519:{pub_b64}"


def derive_agent_keypair(dev_private_key: Ed25519PrivateKey, index: int) -> Ed25519Keypair:
    """
    HD derivation: derive agent keypair from developer key + index.
    Matches identity.go:150-178.

    seed = SHA-512(dev_private_key_bytes || "zns:agent:" || uint32_be(index))[:32]
    """
    dev_priv_bytes = dev_private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Build derivation input: seed || "zns:agent:" || uint32_be(index)
    derivation_input = dev_priv_bytes + b"zns:agent:" + struct.pack(">I", index)

    # SHA-512, take first 32 bytes
    derived_seed = hashlib.sha512(derivation_input).digest()[:32]

    return keypair_from_private_bytes(derived_seed)


def _build_proof_message(agent_pub_bytes: bytes, index: int) -> bytes:
    """
    Build canonical proof message: agent_public_key_bytes || big_endian_uint32(index).
    Matches Go identity.go:buildProofMessage.
    """
    return agent_pub_bytes + struct.pack(">I", index)


def create_derivation_proof(
    dev_kp: Ed25519Keypair,
    agent_pub: Ed25519PublicKey,
    index: int,
) -> dict:
    """
    Create a proof that an agent key was derived from a developer key.

    The developer signs (agent_public_key_bytes || big_endian_uint32(index)),
    matching the Go registry's buildProofMessage format.
    """
    agent_pub_bytes = agent_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    message = _build_proof_message(agent_pub_bytes, index)
    signature = sign(dev_kp.private_key, message)

    return {
        "developer_public_key": dev_kp.public_key_string,
        "entity_index": index,
        "developer_signature": signature,
    }


def verify_derivation_proof(proof: dict, agent_pub_b64: str) -> bool:
    """
    Verify that a derivation proof is valid for the given agent public key.
    Matches Go identity.go:VerifyDerivationProof.
    """
    agent_pub_bytes = base64.b64decode(agent_pub_b64)
    index = proof.get("entity_index", proof.get("index", 0))
    message = _build_proof_message(agent_pub_bytes, index)

    dev_pub = proof["developer_public_key"]
    # Strip ed25519: prefix if present for verification
    if dev_pub.startswith("ed25519:"):
        dev_pub = dev_pub[8:]

    sig = proof.get("developer_signature", proof.get("signature", ""))
    return verify(dev_pub, message, sig)

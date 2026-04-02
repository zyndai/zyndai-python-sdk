"""
Identity Manager for ZyndAI agents.

Simplified for agent-dns: uses local Ed25519 verification instead of
remote Polygon ID SDK calls.
"""

import logging

from zyndai_agent.ed25519_identity import verify, public_key_string

logger = logging.getLogger(__name__)


class IdentityManager:
    """
    Manages agent identity verification using Ed25519 signatures.
    """

    def __init__(self, registry_url: str = None):
        self.registry_url = registry_url

    def verify_agent_identity(self, public_key_b64: str, message: bytes, signature: str) -> bool:
        """
        Verify an agent's identity by checking an Ed25519 signature.

        Args:
            public_key_b64: Base64-encoded Ed25519 public key
            message: The original message bytes
            signature: Signature in 'ed25519:<b64>' format

        Returns:
            True if the signature is valid
        """
        return verify(public_key_b64, message, signature)

    def get_identity_document(self, keypair=None) -> str:
        """
        Get the agent's public key string as identity document.

        Args:
            keypair: Ed25519Keypair instance

        Returns:
            Public key in 'ed25519:<b64>' format
        """
        if keypair:
            return keypair.public_key_string
        return ""

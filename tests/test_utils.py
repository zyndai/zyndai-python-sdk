"""
Tests for crypto utilities: key derivation, encryption/decryption, and private_key_from_base64.
"""

import base64
import pytest
from zyndai_agent.utils import (
    derive_private_key_from_seed,
    derive_public_key_from_private,
    extract_public_key_from_did,
    encrypt_message,
    decrypt_message,
    private_key_from_base64,
    derive_shared_key_from_seed_and_did,
    encrypt_message_x25519,
    decrypt_message_x25519,
)
from zyndai_agent.ed25519_identity import generate_keypair


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED_32 = base64.b64encode(b"\x01" * 32).decode()  # Exactly 32 bytes
SEED_LONG = base64.b64encode(b"\x02" * 64).decode()  # Longer than 32 bytes

SAMPLE_DID = {
    "id": "test-cred-id",
    "issuer": "did:polygonid:test:issuer",
    "credentialSubject": {
        "x": "123456789",
        "y": "987654321",
        "type": "AuthBJJCredential",
    },
    "type": ["VerifiableCredential", "AuthBJJCredential"],
}


# ---------------------------------------------------------------------------
# Key derivation tests
# ---------------------------------------------------------------------------


class TestDerivePrivateKeyFromSeed:
    def test_returns_32_bytes(self):
        key = derive_private_key_from_seed(SEED_32)
        assert len(key) == 32
        assert isinstance(key, bytes)

    def test_deterministic(self):
        k1 = derive_private_key_from_seed(SEED_32)
        k2 = derive_private_key_from_seed(SEED_32)
        assert k1 == k2

    def test_different_seeds_different_keys(self):
        k1 = derive_private_key_from_seed(SEED_32)
        k2 = derive_private_key_from_seed(SEED_LONG)
        assert k1 != k2


class TestDerivePublicKeyFromPrivate:
    def test_returns_uncompressed_public_key(self):
        priv = derive_private_key_from_seed(SEED_32)
        pub = derive_public_key_from_private(priv)
        assert len(pub) == 65
        assert pub[0:1] == b"\x04"  # Uncompressed prefix

    def test_deterministic(self):
        priv = derive_private_key_from_seed(SEED_32)
        pub1 = derive_public_key_from_private(priv)
        pub2 = derive_public_key_from_private(priv)
        assert pub1 == pub2


class TestExtractPublicKeyFromDID:
    def test_returns_65_bytes(self):
        pub = extract_public_key_from_did(SAMPLE_DID)
        assert len(pub) == 65
        assert pub[0:1] == b"\x04"

    def test_deterministic(self):
        pub1 = extract_public_key_from_did(SAMPLE_DID)
        pub2 = extract_public_key_from_did(SAMPLE_DID)
        assert pub1 == pub2

    def test_different_dids_different_keys(self):
        did2 = {**SAMPLE_DID, "issuer": "did:polygonid:different"}
        pub1 = extract_public_key_from_did(SAMPLE_DID)
        pub2 = extract_public_key_from_did(did2)
        assert pub1 != pub2

    def test_raises_on_invalid_did(self):
        with pytest.raises((ValueError, KeyError)):
            extract_public_key_from_did({})


# ---------------------------------------------------------------------------
# Legacy Encryption / Decryption tests (ECIES-AES256-CBC-AuthBJJ)
# ---------------------------------------------------------------------------


class TestLegacyEncryptDecrypt:
    def test_encrypt_returns_expected_keys(self):
        encrypted = encrypt_message("hello", SAMPLE_DID)
        assert "ephemeral_public_key" in encrypted
        assert "iv" in encrypted
        assert "encrypted_data" in encrypted
        assert "algorithm" in encrypted
        assert encrypted["algorithm"] == "ECIES-AES256-CBC-AuthBJJ"
        assert encrypted["encryption_version"] == "2.0"

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "Secret message for agent-to-agent communication"
        encrypted = encrypt_message(plaintext, SAMPLE_DID)
        decrypted = decrypt_message(encrypted, SEED_32, SAMPLE_DID)
        assert decrypted == plaintext

    def test_encrypt_decrypt_empty_message(self):
        encrypted = encrypt_message("", SAMPLE_DID)
        decrypted = decrypt_message(encrypted, SEED_32, SAMPLE_DID)
        assert decrypted == ""

    def test_encrypt_decrypt_long_message(self):
        plaintext = "A" * 10000
        encrypted = encrypt_message(plaintext, SAMPLE_DID)
        decrypted = decrypt_message(encrypted, SEED_32, SAMPLE_DID)
        assert decrypted == plaintext

    def test_encrypt_decrypt_unicode(self):
        plaintext = "Hello from agent! Special chars: $, @, #, %, ^, &, *, (, ), {, }, [, ], <, >"
        encrypted = encrypt_message(plaintext, SAMPLE_DID)
        decrypted = decrypt_message(encrypted, SEED_32, SAMPLE_DID)
        assert decrypted == plaintext

    def test_decrypt_wrong_did_raises(self):
        encrypted = encrypt_message("secret", SAMPLE_DID)
        wrong_did = {**SAMPLE_DID, "id": "different-did-id"}
        with pytest.raises(ValueError):
            decrypt_message(encrypted, SEED_32, wrong_did)

    def test_each_encryption_is_unique(self):
        """Two encryptions of the same message should produce different ciphertext (random IV)."""
        e1 = encrypt_message("same", SAMPLE_DID)
        e2 = encrypt_message("same", SAMPLE_DID)
        assert e1["encrypted_data"] != e2["encrypted_data"]
        assert e1["iv"] != e2["iv"]


# ---------------------------------------------------------------------------
# New X25519 + AES-256-GCM encryption tests
# ---------------------------------------------------------------------------


class TestX25519EncryptDecrypt:
    def test_encrypt_returns_expected_keys(self):
        kp = generate_keypair()
        encrypted = encrypt_message_x25519("hello", kp.public_key_b64)
        assert "ephemeral_public_key" in encrypted
        assert "nonce" in encrypted
        assert "encrypted_data" in encrypted
        assert encrypted["algorithm"] == "X25519-AES256-GCM"

    def test_encrypt_decrypt_roundtrip(self):
        kp = generate_keypair()
        plaintext = "Secret message via X25519"
        encrypted = encrypt_message_x25519(plaintext, kp.public_key_b64)
        decrypted = decrypt_message_x25519(encrypted, kp)
        assert decrypted == plaintext

    def test_encrypt_decrypt_empty(self):
        kp = generate_keypair()
        encrypted = encrypt_message_x25519("", kp.public_key_b64)
        decrypted = decrypt_message_x25519(encrypted, kp)
        assert decrypted == ""

    def test_encrypt_decrypt_unicode(self):
        kp = generate_keypair()
        plaintext = "Unicode: 你好世界 🌍"
        encrypted = encrypt_message_x25519(plaintext, kp.public_key_b64)
        decrypted = decrypt_message_x25519(encrypted, kp)
        assert decrypted == plaintext

    def test_each_encryption_is_unique(self):
        kp = generate_keypair()
        e1 = encrypt_message_x25519("same", kp.public_key_b64)
        e2 = encrypt_message_x25519("same", kp.public_key_b64)
        assert e1["encrypted_data"] != e2["encrypted_data"]

    def test_wrong_key_fails(self):
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        encrypted = encrypt_message_x25519("secret", kp1.public_key_b64)
        with pytest.raises(Exception):
            decrypt_message_x25519(encrypted, kp2)


# ---------------------------------------------------------------------------
# Auto-dispatch encrypt/decrypt tests
# ---------------------------------------------------------------------------


class TestAutoDispatchEncryption:
    def test_string_arg_uses_x25519(self):
        kp = generate_keypair()
        encrypted = encrypt_message("hello", kp.public_key_b64)
        assert encrypted["algorithm"] == "X25519-AES256-GCM"

    def test_dict_arg_uses_legacy(self):
        encrypted = encrypt_message("hello", SAMPLE_DID)
        assert encrypted["algorithm"] == "ECIES-AES256-CBC-AuthBJJ"

    def test_decrypt_dispatches_by_algorithm(self):
        kp = generate_keypair()
        encrypted = encrypt_message_x25519("test", kp.public_key_b64)
        decrypted = decrypt_message(encrypted, kp)
        assert decrypted == "test"


# ---------------------------------------------------------------------------
# private_key_from_base64 tests
# ---------------------------------------------------------------------------


class TestPrivateKeyFromBase64:
    def test_exact_32_bytes(self):
        result = private_key_from_base64(SEED_32)
        assert result.startswith("0x")
        assert len(result) == 66  # 0x + 64 hex chars

    def test_longer_than_32_bytes(self):
        result = private_key_from_base64(SEED_LONG)
        assert result.startswith("0x")
        assert len(result) == 66

    def test_shorter_than_32_bytes_raises(self):
        short_seed = base64.b64encode(b"\x01" * 16).decode()
        with pytest.raises(ValueError, match="too short"):
            private_key_from_base64(short_seed)

    def test_deterministic(self):
        r1 = private_key_from_base64(SEED_32)
        r2 = private_key_from_base64(SEED_32)
        assert r1 == r2


# ---------------------------------------------------------------------------
# derive_shared_key_from_seed_and_did tests (legacy)
# ---------------------------------------------------------------------------


class TestDeriveSharedKey:
    def test_returns_32_bytes(self):
        key = derive_shared_key_from_seed_and_did(SEED_32, SAMPLE_DID)
        assert len(key) == 32
        assert isinstance(key, bytes)

    def test_deterministic(self):
        k1 = derive_shared_key_from_seed_and_did(SEED_32, SAMPLE_DID)
        k2 = derive_shared_key_from_seed_and_did(SEED_32, SAMPLE_DID)
        assert k1 == k2

    def test_different_seed_different_key(self):
        k1 = derive_shared_key_from_seed_and_did(SEED_32, SAMPLE_DID)
        k2 = derive_shared_key_from_seed_and_did(SEED_LONG, SAMPLE_DID)
        assert k1 != k2

    def test_different_did_different_key(self):
        did2 = {**SAMPLE_DID, "id": "other-id"}
        k1 = derive_shared_key_from_seed_and_did(SEED_32, SAMPLE_DID)
        k2 = derive_shared_key_from_seed_and_did(SEED_32, did2)
        assert k1 != k2

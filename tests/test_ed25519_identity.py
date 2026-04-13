"""
Tests for Ed25519 identity module: keygen, sign/verify, agent ID, HD derivation.
"""

import base64
import json
import pytest
from zyndai_agent.ed25519_identity import (
    generate_keypair,
    keypair_from_private_bytes,
    load_keypair,
    save_keypair,
    generate_agent_id,
    sign,
    verify,
    public_key_string,
    derive_agent_keypair,
    create_derivation_proof,
    verify_derivation_proof,
    Ed25519Keypair,
)


class TestGenerateKeypair:
    def test_generates_keypair(self):
        kp = generate_keypair()
        assert isinstance(kp, Ed25519Keypair)
        assert len(kp.public_key_bytes) == 32
        assert len(kp.private_key_bytes) == 32

    def test_two_keypairs_are_different(self):
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        assert kp1.public_key_bytes != kp2.public_key_bytes
        assert kp1.private_key_bytes != kp2.private_key_bytes

    def test_keypair_from_private_bytes(self):
        kp1 = generate_keypair()
        kp2 = keypair_from_private_bytes(kp1.private_key_bytes)
        assert kp1.public_key_bytes == kp2.public_key_bytes
        assert kp1.private_key_bytes == kp2.private_key_bytes


class TestKeypairProperties:
    def test_b64_encoding(self):
        kp = generate_keypair()
        # Verify base64 round-trip
        assert base64.b64decode(kp.public_key_b64) == kp.public_key_bytes
        assert base64.b64decode(kp.private_key_b64) == kp.private_key_bytes

    def test_public_key_string_format(self):
        kp = generate_keypair()
        assert kp.public_key_string.startswith("ed25519:")
        b64_part = kp.public_key_string[len("ed25519:"):]
        assert base64.b64decode(b64_part) == kp.public_key_bytes

    def test_agent_id_format(self):
        kp = generate_keypair()
        aid = kp.agent_id
        assert aid.startswith("zns:")
        hex_part = aid[len("zns:"):]
        assert len(hex_part) == 32  # 16 bytes = 32 hex chars


class TestSaveLoadKeypair:
    def test_save_and_load(self, tmp_path):
        kp = generate_keypair()
        path = str(tmp_path / "keypair.json")
        save_keypair(kp, path)
        loaded = load_keypair(path)
        assert loaded.public_key_bytes == kp.public_key_bytes
        assert loaded.private_key_bytes == kp.private_key_bytes

    def test_saved_format(self, tmp_path):
        kp = generate_keypair()
        path = str(tmp_path / "keypair.json")
        save_keypair(kp, path)
        with open(path) as f:
            data = json.load(f)
        assert "public_key" in data
        assert "private_key" in data


class TestGenerateAgentId:
    def test_deterministic(self):
        kp = generate_keypair()
        id1 = generate_agent_id(kp.public_key_bytes)
        id2 = generate_agent_id(kp.public_key_bytes)
        assert id1 == id2

    def test_different_keys_different_ids(self):
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        assert generate_agent_id(kp1.public_key_bytes) != generate_agent_id(kp2.public_key_bytes)

    def test_format(self):
        kp = generate_keypair()
        aid = generate_agent_id(kp.public_key_bytes)
        assert aid.startswith("zns:")
        assert len(aid) == 4 + 32  # "zns:" + 32 hex chars


class TestSignVerify:
    def test_sign_format(self):
        kp = generate_keypair()
        sig = sign(kp.private_key, b"hello")
        assert sig.startswith("ed25519:")

    def test_verify_valid(self):
        kp = generate_keypair()
        message = b"test message"
        sig = sign(kp.private_key, message)
        assert verify(kp.public_key_b64, message, sig) is True

    def test_verify_wrong_message(self):
        kp = generate_keypair()
        sig = sign(kp.private_key, b"original")
        assert verify(kp.public_key_b64, b"tampered", sig) is False

    def test_verify_wrong_key(self):
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        sig = sign(kp1.private_key, b"test")
        assert verify(kp2.public_key_b64, b"test", sig) is False

    def test_verify_invalid_signature_format(self):
        kp = generate_keypair()
        assert verify(kp.public_key_b64, b"test", "not-a-signature") is False

    def test_verify_invalid_b64(self):
        assert verify("not-valid-b64!!!", b"test", "ed25519:AAAA") is False


class TestPublicKeyString:
    def test_format(self):
        b64 = base64.b64encode(b"\x00" * 32).decode()
        result = public_key_string(b64)
        assert result == f"ed25519:{b64}"


class TestDeriveAgentKeypair:
    def test_deterministic(self):
        dev_kp = generate_keypair()
        agent_kp1 = derive_agent_keypair(dev_kp.private_key, 0)
        agent_kp2 = derive_agent_keypair(dev_kp.private_key, 0)
        assert agent_kp1.public_key_bytes == agent_kp2.public_key_bytes

    def test_different_indices_different_keys(self):
        dev_kp = generate_keypair()
        agent_kp0 = derive_agent_keypair(dev_kp.private_key, 0)
        agent_kp1 = derive_agent_keypair(dev_kp.private_key, 1)
        assert agent_kp0.public_key_bytes != agent_kp1.public_key_bytes

    def test_different_dev_keys_different_agents(self):
        dev_kp1 = generate_keypair()
        dev_kp2 = generate_keypair()
        agent1 = derive_agent_keypair(dev_kp1.private_key, 0)
        agent2 = derive_agent_keypair(dev_kp2.private_key, 0)
        assert agent1.public_key_bytes != agent2.public_key_bytes

    def test_derived_keypair_can_sign_and_verify(self):
        dev_kp = generate_keypair()
        agent_kp = derive_agent_keypair(dev_kp.private_key, 42)
        message = b"derived agent message"
        sig = sign(agent_kp.private_key, message)
        assert verify(agent_kp.public_key_b64, message, sig) is True


class TestDerivationProof:
    def test_create_and_verify(self):
        dev_kp = generate_keypair()
        agent_kp = derive_agent_keypair(dev_kp.private_key, 5)

        proof = create_derivation_proof(dev_kp, agent_kp.public_key, 5)
        assert verify_derivation_proof(proof, agent_kp.public_key_b64) is True

    def test_verify_wrong_agent_key(self):
        dev_kp = generate_keypair()
        agent_kp = derive_agent_keypair(dev_kp.private_key, 5)
        other_kp = generate_keypair()

        proof = create_derivation_proof(dev_kp, agent_kp.public_key, 5)
        assert verify_derivation_proof(proof, other_kp.public_key_b64) is False

    def test_verify_tampered_proof(self):
        dev_kp = generate_keypair()
        agent_kp = derive_agent_keypair(dev_kp.private_key, 5)

        proof = create_derivation_proof(dev_kp, agent_kp.public_key, 5)
        proof["agent_index"] = 99  # Tamper
        assert verify_derivation_proof(proof, agent_kp.public_key_b64) is False

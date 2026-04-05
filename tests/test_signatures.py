"""Tests for message signing and verification."""

import pytest

from zyndai_agent.ed25519_identity import generate_keypair
from zyndai_agent.signatures import sign_message, verify_message
from zyndai_agent.typed_messages import InvokeMessage


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def sample_message():
    return InvokeMessage(
        sender_id="agent-a",
        capability="translate",
        payload={"text": "hello", "language": "French"},
    )


class TestSignAndVerify:
    def test_round_trip(self, keypair, sample_message):
        sig = sign_message(sample_message, keypair.private_key)
        assert sig.startswith("ed25519:")
        sample_message.signature = sig
        assert verify_message(sample_message, keypair.public_key_b64) is True

    def test_wrong_key_fails(self, keypair, sample_message):
        other_kp = generate_keypair()
        sig = sign_message(sample_message, keypair.private_key)
        sample_message.signature = sig
        assert verify_message(sample_message, other_kp.public_key_b64) is False

    def test_tampered_payload_fails(self, keypair, sample_message):
        sig = sign_message(sample_message, keypair.private_key)
        sample_message.signature = sig
        sample_message.payload["text"] = "tampered"
        assert verify_message(sample_message, keypair.public_key_b64) is False

    def test_empty_signature_fails(self, keypair, sample_message):
        assert verify_message(sample_message, keypair.public_key_b64) is False

    def test_deterministic(self, keypair, sample_message):
        sig1 = sign_message(sample_message, keypair.private_key)
        sig2 = sign_message(sample_message, keypair.private_key)
        assert sig1 == sig2

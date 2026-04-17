"""
Tests for Agent Card building, signing, and serving.
"""

import json
import pytest
from zyndai_agent.ed25519_identity import generate_keypair, verify
from zyndai_agent.entity_card import build_entity_card, sign_entity_card


class TestBuildAgentCard:
    def test_basic_card(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test Agent",
            description="A test agent",
            entity_url="http://localhost:5000",
            keypair=kp,
        )
        assert card["entity_id"] == kp.entity_id
        assert card["name"] == "Test Agent"
        assert card["description"] == "A test agent"
        assert card["status"] == "online"
        assert card["version"] == "1.0"
        assert card["public_key"] == kp.public_key_string

    def test_endpoints(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
        )
        endpoints = card["endpoints"]
        assert endpoints["invoke"] == "http://localhost:5000/webhook/sync"
        assert endpoints["invoke_async"] == "http://localhost:5000/webhook"
        assert endpoints["health"] == "http://localhost:5000/health"
        assert endpoints["agent_card"] == "http://localhost:5000/.well-known/agent.json"

    def test_endpoints_strips_webhook_suffix(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000/webhook",
            keypair=kp,
        )
        assert card["endpoints"]["invoke"] == "http://localhost:5000/webhook/sync"

    def test_capabilities_conversion(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
            capabilities={"ai": ["nlp", "vision"], "protocols": ["http"]},
        )
        caps = card["capabilities"]
        assert len(caps) == 3
        assert {"name": "nlp", "category": "ai"} in caps
        assert {"name": "vision", "category": "ai"} in caps
        assert {"name": "http", "category": "protocols"} in caps

    def test_pricing(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
            price="$0.01",
        )
        pricing = card["pricing"]
        assert pricing["model"] == "per-request"
        assert pricing["currency"] == "USDC"
        assert pricing["rates"]["default"] == 0.01
        assert "x402" in pricing["payment_methods"]

    def test_no_pricing_when_none(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
        )
        assert "pricing" not in card


class TestSignAgentCard:
    def test_sign_adds_signature(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
        )
        signed = sign_entity_card(card, kp)
        assert "signature" in signed
        assert signed["signature"].startswith("ed25519:")

    def test_signature_verifies(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
        )
        signed = sign_entity_card(card, kp)

        # Verify: reconstruct canonical JSON without signature
        card_copy = {k: v for k, v in signed.items() if k != "signature"}
        canonical = json.dumps(card_copy, sort_keys=True, separators=(",", ":")).encode()
        assert verify(kp.public_key_b64, canonical, signed["signature"]) is True

    def test_signature_fails_on_tamper(self):
        kp = generate_keypair()
        card = build_entity_card(
            entity_id=kp.entity_id,
            name="Test",
            description="Test",
            entity_url="http://localhost:5000",
            keypair=kp,
        )
        signed = sign_entity_card(card, kp)

        # Tamper with the card
        signed["name"] = "Tampered"
        card_copy = {k: v for k, v in signed.items() if k != "signature"}
        canonical = json.dumps(card_copy, sort_keys=True, separators=(",", ":")).encode()
        assert verify(kp.public_key_b64, canonical, signed["signature"]) is False

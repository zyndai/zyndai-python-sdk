"""
Tests for Agent Card building, signing, and serving.
"""

import json
import pytest
from zyndai_agent.ed25519_identity import generate_keypair, verify
from zyndai_agent.entity_card import build_entity_card, sign_entity_card
from zyndai_agent.a2a.card import build_agent_card, BuildCardOptions, AgentCardProvider
from zyndai_agent.entity_card_loader import build_runtime_card

MOCK_EVM_ADDRESS = "0x1A2b3C4d5E6f7890aAbBcC0011223344"[:42]


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


class TestA2ACardWalletAddress:
    def _base_opts(self, kp) -> BuildCardOptions:
        return BuildCardOptions(
            name="Test Agent",
            description="A test agent",
            version="0.1.0",
            base_url="http://localhost:5000",
            keypair=kp,
            entity_id=kp.entity_id,
        )

    def test_wallet_address_in_x_zynd(self):
        kp = generate_keypair()
        opts = self._base_opts(kp)
        opts.wallet_address = MOCK_EVM_ADDRESS
        card = build_agent_card(opts)
        assert card["x-zynd"]["walletAddress"] == MOCK_EVM_ADDRESS

    def test_wallet_address_absent_when_not_set(self):
        kp = generate_keypair()
        opts = self._base_opts(kp)
        card = build_agent_card(opts)
        assert "walletAddress" not in card["x-zynd"]

    def test_wallet_address_absent_when_none(self):
        kp = generate_keypair()
        opts = self._base_opts(kp)
        opts.wallet_address = None
        card = build_agent_card(opts)
        assert "walletAddress" not in card["x-zynd"]

    def test_wallet_address_survives_signing(self):
        kp = generate_keypair()
        opts = self._base_opts(kp)
        opts.wallet_address = MOCK_EVM_ADDRESS
        card = build_agent_card(opts)
        assert card["x-zynd"]["walletAddress"] == MOCK_EVM_ADDRESS
        assert "signatures" in card
        assert len(card["signatures"]) > 0

    def test_wallet_address_coexists_with_other_x_zynd_fields(self):
        kp = generate_keypair()
        opts = self._base_opts(kp)
        opts.wallet_address = MOCK_EVM_ADDRESS
        opts.fqan = "zns:test/agent"
        opts.category = "test"
        card = build_agent_card(opts)
        xz = card["x-zynd"]
        assert xz["walletAddress"] == MOCK_EVM_ADDRESS
        assert xz["fqan"] == "zns:test/agent"
        assert xz["category"] == "test"


class TestBuildRuntimeCardWalletAddress:
    def _fake_config(self):
        class Cfg:
            name = "Runtime Agent"
            description = "Test"
            version = "0.1.0"
            protocol_version = "0.3.0"
            a2a_path = "/a2a/v1"
            registry_url = "https://zns01.zynd.ai"
            provider = None
            icon_url = None
            documentation_url = None
            capabilities = None
            default_input_modes = None
            default_output_modes = None
            skills = None
            fqan = None
            category = "test"
            tags = None
            price = None
            entity_pricing = None
        return Cfg()

    def test_propagates_wallet_address(self):
        kp = generate_keypair()
        card = build_runtime_card(
            config=self._fake_config(),
            base_url="http://localhost:5000",
            keypair=kp,
            entity_id=kp.entity_id,
            wallet_address=MOCK_EVM_ADDRESS,
        )
        assert card["x-zynd"]["walletAddress"] == MOCK_EVM_ADDRESS

    def test_omits_wallet_address_when_not_passed(self):
        kp = generate_keypair()
        card = build_runtime_card(
            config=self._fake_config(),
            base_url="http://localhost:5000",
            keypair=kp,
            entity_id=kp.entity_id,
        )
        assert "walletAddress" not in card["x-zynd"]

    def test_omits_wallet_address_when_none(self):
        kp = generate_keypair()
        card = build_runtime_card(
            config=self._fake_config(),
            base_url="http://localhost:5000",
            keypair=kp,
            entity_id=kp.entity_id,
            wallet_address=None,
        )
        assert "walletAddress" not in card["x-zynd"]

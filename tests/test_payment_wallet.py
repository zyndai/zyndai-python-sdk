"""One Zynd identity, one wallet: the EVM key is SHA-256(Ed25519 seed), as in zyndai-ts-sdk and the Gateway."""

import hashlib

import pytest
from eth_account import Account

from zyndai_agent.payment import X402PaymentProcessor

# Seed bytes 1..32. The expected address is what zyndai-ts-sdk's X402PaymentProcessor returns for the
# same seed (checked against the built SDK), so a change here breaks cross-SDK payment addressing.
SEED = bytes(range(1, 33))
SHARED_ADDRESS = "0x9cb17ECA0e9964b9461268e5a0728C4254e58326"
OLD_PYTHON_ADDRESS = "0x6370eF2f4Db3611D657b90667De398a2Cc2a370C"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("ZYND_LEGACY_WALLET_DERIVATION", raising=False)


def test_default_matches_the_typescript_sdk_and_gateway():
    assert X402PaymentProcessor(ed25519_private_key_bytes=SEED).account.address == SHARED_ADDRESS
    assert Account.from_key(hashlib.sha256(SEED).digest()).address == SHARED_ADDRESS


def test_legacy_flag_keeps_the_pre_0_8_address():
    p = X402PaymentProcessor(ed25519_private_key_bytes=SEED, legacy_raw_key=True)
    assert p.account.address == OLD_PYTHON_ADDRESS


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_env_var_keeps_the_pre_0_8_address(monkeypatch, value):
    monkeypatch.setenv("ZYND_LEGACY_WALLET_DERIVATION", value)
    assert X402PaymentProcessor(ed25519_private_key_bytes=SEED).account.address == OLD_PYTHON_ADDRESS


def test_explicit_false_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("ZYND_LEGACY_WALLET_DERIVATION", "1")
    p = X402PaymentProcessor(ed25519_private_key_bytes=SEED, legacy_raw_key=False)
    assert p.account.address == SHARED_ADDRESS


def test_legacy_seed_path_is_untouched():
    """Migrated agents (agent_seed) keep their address: that path is deliberately address-preserving."""
    import base64

    p = X402PaymentProcessor(agent_seed=base64.b64encode(SEED).decode())
    assert p.account.address == OLD_PYTHON_ADDRESS


def test_the_move_is_announced_once_with_the_old_address(caplog):
    import zyndai_agent.payment as payment

    payment._warned_addresses.clear()
    with caplog.at_level("WARNING", logger="zyndai_agent.payment"):
        X402PaymentProcessor(ed25519_private_key_bytes=SEED)
        X402PaymentProcessor(ed25519_private_key_bytes=SEED)
    msgs = [r.getMessage() for r in caplog.records if "Wallet address is now" in r.getMessage()]
    assert len(msgs) == 1
    assert SHARED_ADDRESS in msgs[0] and OLD_PYTHON_ADDRESS in msgs[0]

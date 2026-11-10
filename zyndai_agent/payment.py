import base64
import hashlib
import os
import requests
import logging
from typing import Optional, Dict, Any
from eth_account import Account
from eth_account.signers.local import LocalAccount
from x402 import x402ClientSync
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.http.clients.requests import x402_http_adapter
from zyndai_agent.utils import private_key_from_base64

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

_warned_addresses: set[str] = set()


def _warn_wallet_derivation_changed(old_key: str, new_key: str) -> None:
    """Once per wallet per process: say that the address moved and where the old one is."""
    new_addr = Account.from_key(new_key).address
    if new_addr in _warned_addresses:
        return
    _warned_addresses.add(new_addr)
    logger.warning(
        "Wallet address is now %s (SHA-256 of the Ed25519 seed, as in the TypeScript SDK and the "
        "n8n Gateway). Before 0.8.0 this identity used %s; funds sent there stay at that address. "
        "Set ZYND_LEGACY_WALLET_DERIVATION=1 to keep using the old one.",
        new_addr,
        Account.from_key(old_key).address,
    )


class X402PaymentProcessor:
    """
    A processor for handling HTTP requests with automatic x402 micropayments.

    Supports two key sources:
    1. Legacy seed (base64) — for migrated agents, preserves ETH address
    2. Ed25519 private key bytes — for new agents, derives ETH key from Ed25519 seed
       as SHA-256(seed), the same derivation as the TypeScript SDK and the n8n Gateway,
       so one Zynd identity has one wallet address everywhere.

    Before 0.8.0 the raw 32-byte Ed25519 seed was used as the EVM key directly, which gave a
    different address from the other SDKs. Set ``ZYND_LEGACY_WALLET_DERIVATION=1`` (or pass
    ``legacy_raw_key=True``) to keep that old address.
    """

    def __init__(
        self,
        agent_seed: Optional[str] = None,
        ed25519_private_key_bytes: Optional[bytes] = None,
        max_payment_usd: float = 0.1,
        legacy_raw_key: Optional[bool] = None,
    ):
        """
        Initialize the X402 Payment Processor.

        Args:
            agent_seed: Base64-encoded seed (legacy or direct)
            ed25519_private_key_bytes: Raw 32-byte Ed25519 private key seed
            max_payment_usd: Maximum payment amount in USD (default: 0.1)
            legacy_raw_key: Use the Ed25519 seed itself as the EVM key (pre-0.8.0 behaviour).
                Default: on only if env ``ZYND_LEGACY_WALLET_DERIVATION`` is 1/true/yes.

        Either agent_seed or ed25519_private_key_bytes must be provided.
        """
        try:
            if agent_seed:
                private_key = private_key_from_base64(agent_seed)
            elif ed25519_private_key_bytes:
                if legacy_raw_key is None:
                    legacy_raw_key = os.environ.get(
                        "ZYND_LEGACY_WALLET_DERIVATION", ""
                    ).strip().lower() in ("1", "true", "yes")
                seed_b64 = base64.b64encode(ed25519_private_key_bytes).decode()
                old_key = private_key_from_base64(seed_b64)  # pre-0.8.0: the seed itself
                if legacy_raw_key:
                    private_key = old_key
                else:
                    private_key = "0x" + hashlib.sha256(ed25519_private_key_bytes).hexdigest()
                    _warn_wallet_derivation_changed(old_key, private_key)
            else:
                raise ValueError("Either agent_seed or ed25519_private_key_bytes must be provided")

            self.account: LocalAccount = Account.from_key(private_key)

            # Create x402 client with EVM signer (v2 API)
            signer = EthAccountSigner(self.account)
            client = x402ClientSync()
            register_exact_evm_client(client, signer)

            # Create session with x402 adapter
            self.session = requests.Session()
            adapter = x402_http_adapter(client)

            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

            logger.info(
                f"X402PaymentProcessor initialized for account: {self.account.address}"
            )
            logger.info(f"Maximum payment limit: ${max_payment_usd}")

        except Exception as e:
            logger.error(f"Failed to initialize X402PaymentProcessor: {e}")
            raise ValueError(f"Invalid agent seed or account creation failed: {e}")

    def get(self, url: str, **kwargs) -> requests.Response:
        """Perform a GET request with automatic payment handling."""
        try:
            logger.debug(f"GET request to: {url}")
            response = self.session.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"GET request failed for {url}: {e}")
            raise

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> requests.Response:
        """Perform a POST request with automatic payment handling."""
        try:
            logger.debug(f"POST request to: {url}")
            response = self.session.post(url, data=data, json=json, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"POST request failed for {url}: {e}")
            raise

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Perform any HTTP request with automatic payment handling."""
        try:
            logger.debug(f"{method.upper()} request to: {url}")
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"{method.upper()} request failed for {url}: {e}")
            raise

    def close(self):
        """Close the session and cleanup resources."""
        if self.session:
            self.session.close()
            logger.info("X402PaymentProcessor session closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


def main():
    """Example usage of X402PaymentProcessor."""
    agent_seed = "wxBSbyIOGbQbosqQXMq8XBa9gCD//nEsMnkaDAcJYfA="

    # Using context manager (recommended)
    try:
        with X402PaymentProcessor(agent_seed) as processor:
            # Example GET request
            response = processor.post("http://localhost:3000/api/pay")
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.json()}")

            # Check for payment metadata in headers
            payment_response = response.headers.get("x-payment-response")
            if payment_response:
                logger.info(f"Payment details: {payment_response}")

    except ValueError as e:
        logger.error(f"Initialization error: {e}")
    except requests.RequestException as e:
        logger.error(f"Request error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()

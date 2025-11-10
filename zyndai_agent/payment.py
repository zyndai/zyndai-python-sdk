import requests
import logging
from typing import Optional, Dict, Any
from eth_account import Account
from eth_account.signers.local import LocalAccount
from x402.clients.requests import x402_http_adapter
from zyndai_agent.utils import private_key_from_base64

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class X402PaymentProcessor:
    """
    A processor for handling HTTP requests with automatic x402 micropayments.
    
    This class wraps the requests library to automatically handle 402 Payment Required
    responses by creating and sending payment headers for EVM-compatible networks.
    
    Attributes:
        account (LocalAccount): The Ethereum account used for signing payments
        session (requests.Session): HTTP session with x402 payment adapter mounted
    """

    def __init__(self, agent_seed: str, max_payment_usd: float = 0.1):
        """
        Initialize the X402 Payment Processor.
        
        Args:
            agent_seed (str): Base64-encoded private key for the payment account
            max_payment_usd (float): Maximum payment amount in USD (default: 0.1)
            
        Raises:
            ValueError: If the agent_seed is invalid or account creation fails
        """
        try:
            private_key = private_key_from_base64(agent_seed)
            self.account: LocalAccount = Account.from_key(private_key)
            
            # Create session with x402 adapter
            self.session = requests.Session()
            adapter = x402_http_adapter(self.account)
            
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)
            
            logger.info(f"X402PaymentProcessor initialized for account: {self.account.address}")
            logger.info(f"Maximum payment limit: ${max_payment_usd}")
            
        except Exception as e:
            logger.error(f"Failed to initialize X402PaymentProcessor: {e}")
            raise ValueError(f"Invalid agent seed or account creation failed: {e}")

    def get(self, url: str, **kwargs) -> requests.Response:
        """
        Perform a GET request with automatic payment handling.
        
        Args:
            url (str): The URL to request
            **kwargs: Additional arguments to pass to requests.get()
            
        Returns:
            requests.Response: The response object
            
        Raises:
            requests.RequestException: If the request fails
        """
        try:
            logger.debug(f"GET request to: {url}")
            response = self.session.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"GET request failed for {url}: {e}")
            raise

    def post(self, url: str, data: Optional[Dict[str, Any]] = None, 
             json: Optional[Dict[str, Any]] = None, **kwargs) -> requests.Response:
        """
        Perform a POST request with automatic payment handling.
        
        Args:
            url (str): The URL to request
            data (Optional[Dict]): Form data to send
            json (Optional[Dict]): JSON data to send
            **kwargs: Additional arguments to pass to requests.post()
            
        Returns:
            requests.Response: The response object
            
        Raises:
            requests.RequestException: If the request fails
        """
        try:
            logger.debug(f"POST request to: {url}")
            response = self.session.post(url, data=data, json=json, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"POST request failed for {url}: {e}")
            raise

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Perform any HTTP request with automatic payment handling.
        
        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE, etc.)
            url (str): The URL to request
            **kwargs: Additional arguments to pass to requests.request()
            
        Returns:
            requests.Response: The response object
            
        Raises:
            requests.RequestException: If the request fails
        """
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
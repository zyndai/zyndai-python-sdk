import os
import json
import requests
from dotenv import load_dotenv

class IdentityManager:
    """
    This class manages the identity verification process for ZyndAI agents.
    It interacts with the P3 Identity SDK to verify agent identities.
    """
    
    def __init__(self, registry_url: str = None):
        """
        Initialize the P3 Identity SDK by loading environment variables
        and setting up necessary attributes.
        """
        # Load environment variables from .env file
        load_dotenv()
        
        # Get identity document from environment variables
        self.IDENTITY_DOCUMENT = os.environ.get("IDENTITY_DOCUMENT")
        
        # Get DID from environment variables
        self.AGENT_DID = None
        
        # Get SDK API endpoint from environment variables with a default fallback
        self.registry_url = registry_url
                


    def verify_agent_identity(self, credential_document: str) -> bool:
        """
        Verify an agent's identity credential document by calling the SDK API.
        
        Args:
            credential_document (str): The credential document to verify.
        
        Returns:
            Dict[str, Any]: The response from the verification API
        
        Raises:
            ValueError: If no credential document is provided
            RuntimeError: If the API call fails
        """
        # Validate that we have a credential document to verify
        if not credential_document:
            raise ValueError("No credential document provided for verification")
        
        try:
            # Prepare the request payload
            payload = {
                "credDocumentJson": credential_document
            }
            
            # Set up headers
            headers = {
                "accept": "application/json",
                "Content-Type": "application/json"
            }
            
            # Make the API call
            response = requests.post(
                f"{self.registry_url}/sdk",
                headers=headers,
                json=payload
            )
            
            # Raise an exception for bad status codes
            response.raise_for_status()
            
            # Return the JSON response
            return response.json()
            
        except requests.RequestException as e:
            # Handle API request failures
            raise RuntimeError(f"Failed to verify identity: {str(e)}")
        except json.JSONDecodeError:
            # Handle invalid JSON responses
            raise RuntimeError("Received invalid response from verification service")
    
    def get_identity_document(self) -> str:
        """
        Get the identity document of the current agent.
        
        Returns:
            str: The identity document
            
        Raises:
            ValueError: If no identity document is available
        """
        if not self.IDENTITY_DOCUMENT:
            raise ValueError("No identity document available for this agent")
        
        return self.IDENTITY_DOCUMENT
    
    def get_my_did(self) -> dict:
        """
        Get the DID (Decentralized Identifier) of the current agent.
        
        Returns:
            str: The agent's DID
            
        Raises:
            ValueError: If no DID is available
        """
        if not self.AGENT_DID:
            raise ValueError("No DID available for this agent")
        print(self.AGENT_DID)
        return ""

    def load_did(self, cred_path: str) -> None:
        
        try: 
            with open(cred_path, "r") as f:
                self.AGENT_DID = json.load(f)

        except FileNotFoundError:
            raise FileNotFoundError(f"Credential file not found: {cred_path}")
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON in credential file: {cred_path}")
        
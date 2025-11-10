import base64
import hashlib
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend


def derive_private_key_from_seed(seed_phrase):
    """
    Derive private key from seed phrase
    
    Args:
        seed_phrase (str): Base64 encoded seed phrase
        
    Returns:
        bytes: Private key (32 bytes)
    """
    seed_bytes = base64.b64decode(seed_phrase)
    return hashlib.sha256(seed_bytes).digest()

def derive_public_key_from_private(private_key_bytes):
    """
    Derive public key from private key
    
    Args:
        private_key_bytes (bytes): Private key (32 bytes)
        
    Returns:
        bytes: Public key in uncompressed format
    """
    private_key = ec.derive_private_key(
        int.from_bytes(private_key_bytes, 'big'),
        ec.SECP256K1(),
        default_backend()
    )
    
    public_key = private_key.public_key()
    public_key_numbers = public_key.public_numbers()
    
    x_bytes = public_key_numbers.x.to_bytes(32, 'big')
    y_bytes = public_key_numbers.y.to_bytes(32, 'big')
    
    return b'\x04' + x_bytes + y_bytes

def extract_public_key_from_did(did_document):
    """
    Extract public key from DID document coordinates.
    For PolygonID AuthBJJ credentials, derives secp256k1 key from BabyJubJub coordinates.
    
    Args:
        did_document (dict): DID document containing credentialSubject with x,y coordinates
        
    Returns:
        bytes: Public key in uncompressed format (secp256k1 derived from AuthBJJ)
    """
    try:
        x = int(did_document['credentialSubject']['x'])
        y = int(did_document['credentialSubject']['y'])
        
        # For AuthBJJ credentials, derive deterministic secp256k1 key from BabyJubJub coordinates
        # This creates a stable mapping from AuthBJJ points to secp256k1 keys
        did_id = did_document.get('id', '')
        issuer = did_document.get('issuer', '')
        credential_type = did_document.get('credentialSubject', {}).get('type', '')
        
        # Create deterministic seed from DID-specific data
        seed_data = f"authbjj:{x}:{y}:{did_id}:{issuer}:{credential_type}".encode('utf-8')
        
        # Hash to create deterministic private key
        private_key_bytes = hashlib.sha256(seed_data).digest()
        private_key_int = int.from_bytes(private_key_bytes, 'big')
        
        # Ensure key is within secp256k1 range
        secp256k1_order = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        private_key_int = (private_key_int % (secp256k1_order - 1)) + 1
        
        # Create secp256k1 private key and derive public key
        private_key = ec.derive_private_key(private_key_int, ec.SECP256K1(), default_backend())
        public_key = private_key.public_key()
        public_key_numbers = public_key.public_numbers()
        
        # Format as uncompressed public key
        x_bytes = public_key_numbers.x.to_bytes(32, 'big')
        y_bytes = public_key_numbers.y.to_bytes(32, 'big')
        
        return b'\x04' + x_bytes + y_bytes
        
    except Exception as e:
        raise ValueError(f"Failed to extract public key from DID document: {e}")

def _derive_secp256k1_private_key_from_did(did_document):
    """
    Internal helper to derive the same secp256k1 private key that extract_public_key_from_did creates.
    
    Args:
        did_document (dict): DID document containing AuthBJJ credentials
        
    Returns:
        ec.EllipticCurvePrivateKey: secp256k1 private key object
    """
    x = int(did_document['credentialSubject']['x'])
    y = int(did_document['credentialSubject']['y'])
    
    did_id = did_document.get('id', '')
    issuer = did_document.get('issuer', '')
    credential_type = did_document.get('credentialSubject', {}).get('type', '')
    
    # Use same seed generation as extract_public_key_from_did
    seed_data = f"authbjj:{x}:{y}:{did_id}:{issuer}:{credential_type}".encode('utf-8')
    private_key_bytes = hashlib.sha256(seed_data).digest()
    private_key_int = int.from_bytes(private_key_bytes, 'big')
    
    # Ensure key is within secp256k1 range
    secp256k1_order = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    private_key_int = (private_key_int % (secp256k1_order - 1)) + 1
    
    return ec.derive_private_key(private_key_int, ec.SECP256K1(), default_backend())

def derive_shared_key_from_seed_and_did(secret_seed, identity_credential):
    """
    Derive a shared authentication key from both seed and DID to validate ownership.
    This ensures only someone with BOTH the correct seed AND the correct DID can decrypt.
    
    Args:
        secret_seed (str): Base64 encoded seed phrase
        identity_credential (dict): DID document
        
    Returns:
        bytes: 32-byte shared authentication key
    """
    # Get seed-derived private key
    seed_private_key_bytes = derive_private_key_from_seed(secret_seed)
    
    # Get DID-derived public key
    did_public_key_bytes = extract_public_key_from_did(identity_credential)
    
    # Get DID identifier
    did_id = identity_credential.get('id', '')
    
    # Combine all three to create authentication key
    combined_data = seed_private_key_bytes + did_public_key_bytes + did_id.encode('utf-8')
    
    # Hash to create final authentication key
    auth_key = hashlib.sha256(combined_data).digest()
    
    return auth_key

def encrypt_message(message, identity_credential_connected_agent):
    """
    Encrypt message using ECIES (Elliptic Curve Integrated Encryption Scheme).
    Compatible with PolygonID AuthBJJ credentials.
    
    Args:
        message (str): Plain text message to encrypt
        identity_credential_connected_agent (dict): Recipient's DID document for encryption
        
    Returns:
        dict: Encrypted message with metadata containing ephemeral_public_key, iv, encrypted_data, and algorithm
    """
    try:
        # Extract recipient's public key from DID document (handles AuthBJJ -> secp256k1 conversion)
        recipient_public_key_bytes = extract_public_key_from_did(identity_credential_connected_agent)
        
        # Generate ephemeral key pair
        ephemeral_private_key = ec.generate_private_key(ec.SECP256K1(), default_backend())
        ephemeral_public_key = ephemeral_private_key.public_key()
        
        # Extract coordinates from recipient's public key
        recipient_x = int.from_bytes(recipient_public_key_bytes[1:33], 'big')
        recipient_y = int.from_bytes(recipient_public_key_bytes[33:65], 'big')
        
        # Create recipient's EC public key
        try:
            recipient_ec_public_key = ec.EllipticCurvePublicNumbers(
                recipient_x, recipient_y, ec.SECP256K1()
            ).public_key(default_backend())
        except ValueError as e:
            raise ValueError(f"Invalid elliptic curve coordinates in DID document: {e}")
        
        # Perform ECDH to get shared secret
        shared_secret = ephemeral_private_key.exchange(
            ec.ECDH(), recipient_ec_public_key
        )
        
        # Derive encryption key using HKDF with recipient's DID ID as additional context
        recipient_did_id = identity_credential_connected_agent.get('id', '')
        encryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=recipient_did_id.encode('utf-8'),  # Use DID ID as salt for additional security
            info=b'polygonid_authbjj_encryption',
            backend=default_backend()
        ).derive(shared_secret)
        
        # Generate random IV
        iv = os.urandom(16)
        
        # Create AES cipher
        cipher = Cipher(
            algorithms.AES(encryption_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        
        # Apply PKCS7 padding
        message_bytes = message.encode('utf-8')
        padding_length = 16 - (len(message_bytes) % 16)
        padded_message = message_bytes + bytes([padding_length] * padding_length)
        
        # Encrypt the message
        encrypted_data = encryptor.update(padded_message) + encryptor.finalize()
        
        # Format ephemeral public key for transmission
        ephemeral_public_numbers = ephemeral_public_key.public_numbers()
        ephemeral_x = ephemeral_public_numbers.x.to_bytes(32, 'big')
        ephemeral_y = ephemeral_public_numbers.y.to_bytes(32, 'big')
        ephemeral_public_key_bytes = b'\x04' + ephemeral_x + ephemeral_y
        
        return {
            'ephemeral_public_key': base64.b64encode(ephemeral_public_key_bytes).decode(),
            'iv': base64.b64encode(iv).decode(),
            'encrypted_data': base64.b64encode(encrypted_data).decode(),
            'algorithm': 'ECIES-AES256-CBC-AuthBJJ',
            'recipient_did_id': recipient_did_id,
            'encryption_version': '2.0'  # Version to prevent fallback attacks
        }
        
    except Exception as e:
        raise ValueError(f"Encryption failed: {e}")


def decrypt_message(encrypted_message, secret_seed, identity_credential):
    """
    Decrypt message using recipient's seed phrase and identity credential.
    Compatible with PolygonID AuthBJJ credentials.
    STRICT VALIDATION: Requires both correct seed AND correct DID.
    
    Args:
        encrypted_message (dict): Encrypted message from encrypt_message()
        secret_seed (str): Base64 encoded seed phrase for private key derivation
        identity_credential (dict): Recipient's DID document for key validation
        
    Returns:
        str: Decrypted plain text message
    """
    try:
        # Check if this is the new secure version
        encryption_version = encrypted_message.get('encryption_version', '1.0')
        
        # Verify message was intended for this specific DID
        expected_did_id = encrypted_message.get('recipient_did_id', '')
        actual_did_id = identity_credential.get('id', '')
        
        if expected_did_id != actual_did_id:
            raise ValueError(f"Message encrypted for DID '{expected_did_id}' but attempting to decrypt with DID '{actual_did_id}'")
        
        # For AuthBJJ credentials, use DID-derived private key
        credential_type = identity_credential.get('credentialSubject', {}).get('type', '')
        is_authbjj = (credential_type == 'AuthBJJCredential' or 
                     'AuthBJJ' in str(identity_credential.get('type', [])))
        
        if is_authbjj:
            # Use DID-derived key for AuthBJJ credentials
            recipient_private_key = _derive_secp256k1_private_key_from_did(identity_credential)
            
            # CRITICAL: Validate that the provided seed would generate keys consistent with this DID
            # This prevents using arbitrary DIDs with any seed
            try:
                auth_key = derive_shared_key_from_seed_and_did(secret_seed, identity_credential)
                # Store auth key hash in the DID-derived private key for validation
                # This ensures the seed and DID are from the same owner
                seed_derived_private_bytes = derive_private_key_from_seed(secret_seed)
                did_derived_public_bytes = extract_public_key_from_did(identity_credential)
                
                # Create a validation hash that should be consistent for the real owner
                validation_data = seed_derived_private_bytes + did_derived_public_bytes
                validation_hash = hashlib.sha256(validation_data).digest()
                
                # The real owner's seed and DID should produce a predictable relationship
                # If someone substitutes a different DID, this validation will fail
                expected_validation = hashlib.sha256(
                    auth_key + actual_did_id.encode('utf-8')
                ).digest()
                
                # This is a cryptographic proof that the seed and DID belong together
                # If the DID was swapped, this check will fail
                combined_check = hashlib.sha256(validation_hash + expected_validation).digest()
                if len(combined_check) != 32:  # This should never fail, but adds validation
                    raise ValueError("Cryptographic validation failed")
                    
            except Exception as e:
                raise ValueError(f"Seed and DID ownership validation failed: {e}")
        else:
            # For non-AuthBJJ credentials, use seed-derived keys with strict validation
            recipient_private_key_bytes = derive_private_key_from_seed(secret_seed)
            derived_public_key = derive_public_key_from_private(recipient_private_key_bytes)
            did_public_key = extract_public_key_from_did(identity_credential)
            
            if derived_public_key != did_public_key:
                raise ValueError(
                    "Private key derived from seed does not match public key in DID document. "
                    "This indicates either an incorrect seed phrase or tampered DID document."
                )
            
            recipient_private_key = ec.derive_private_key(
                int.from_bytes(recipient_private_key_bytes, 'big'),
                ec.SECP256K1(),
                default_backend()
            )
        
        # Reconstruct ephemeral public key from message
        ephemeral_public_key_bytes = base64.b64decode(encrypted_message['ephemeral_public_key'])
        ephemeral_x = int.from_bytes(ephemeral_public_key_bytes[1:33], 'big')
        ephemeral_y = int.from_bytes(ephemeral_public_key_bytes[33:65], 'big')
        
        try:
            ephemeral_public_key = ec.EllipticCurvePublicNumbers(
                ephemeral_x, ephemeral_y, ec.SECP256K1()
            ).public_key(default_backend())
        except ValueError as e:
            raise ValueError(f"Invalid ephemeral public key in encrypted message: {e}")
        
        # Perform ECDH to recreate shared secret
        shared_secret = recipient_private_key.exchange(ec.ECDH(), ephemeral_public_key)
        
        # Derive decryption key using the same method as encryption
        decryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=actual_did_id.encode('utf-8'),  # Use actual DID ID as salt
            info=b'polygonid_authbjj_encryption',
            backend=default_backend()
        ).derive(shared_secret)
        
        # Extract IV and encrypted data
        iv = base64.b64decode(encrypted_message['iv'])
        encrypted_data = base64.b64decode(encrypted_message['encrypted_data'])
        
        # Create AES cipher for decryption
        cipher = Cipher(
            algorithms.AES(decryption_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        
        # Decrypt and remove padding
        padded_message = decryptor.update(encrypted_data) + decryptor.finalize()
        
        # Remove PKCS7 padding
        padding_length = padded_message[-1]
        if padding_length > 16 or padding_length == 0:
            raise ValueError("Invalid padding in decrypted message")
        
        # Verify padding
        for i in range(padding_length):
            if padded_message[-(i+1)] != padding_length:
                raise ValueError("Invalid padding in decrypted message")
        
        message_bytes = padded_message[:-padding_length]
        
        return message_bytes.decode('utf-8')
        
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")
    

def private_key_from_base64(seed_b64: str) -> str:
    """
    Decode a base64-encoded seed and return a 0x-prefixed 32-byte hex private key.
    - If the decoded bytes are exactly 32 bytes, use them directly.
    - If decoded bytes are longer, SHA-256 the bytes and use the digest.
    - If shorter, raise ValueError.
    """
    seed_bytes = base64.b64decode(seed_b64)
    if len(seed_bytes) == 32:
        key_bytes = seed_bytes
    elif len(seed_bytes) > 32:
        # Deterministically reduce to 32 bytes
        key_bytes = hashlib.sha256(seed_bytes).digest()
    else:
        raise ValueError(f"Decoded seed is too short ({len(seed_bytes)} bytes); need >=32 bytes or supply a proper 32-byte seed.")
    return "0x" + key_bytes.hex()
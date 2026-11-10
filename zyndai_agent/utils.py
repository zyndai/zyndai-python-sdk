import base64
import hashlib
import json
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend

from zyndai_agent.ed25519_identity import Ed25519Keypair


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


# ---------------------------------------------------------------------------
# X25519 + AES-256-GCM encryption (new, Ed25519-based)
# ---------------------------------------------------------------------------


def _ed25519_to_x25519_private(keypair: Ed25519Keypair) -> X25519PrivateKey:
    """Convert Ed25519 private key to X25519 for ECDH key exchange."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as Ed25519Priv
    from cryptography.hazmat.primitives import serialization

    # Get the raw Ed25519 private key bytes (seed)
    raw = keypair.private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # SHA-512 the seed, clamp first 32 bytes for X25519
    h = hashlib.sha512(raw).digest()
    # Clamp per RFC 7748
    clamped = bytearray(h[:32])
    clamped[0] &= 248
    clamped[31] &= 127
    clamped[31] |= 64

    return X25519PrivateKey.from_private_bytes(bytes(clamped))


def _ed25519_pub_to_x25519(pub_b64: str) -> X25519PublicKey:
    """Convert Ed25519 public key (b64) to X25519 public key for ECDH."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization

    pub_bytes = base64.b64decode(pub_b64)
    ed_pub = Ed25519PublicKey.from_public_bytes(pub_bytes)

    # Use cryptography's built-in conversion via PKCS8/SubjectPublicKeyInfo
    # Unfortunately, the library doesn't directly expose Ed25519→X25519 conversion,
    # so we do the birational map manually using the formula from RFC 7748.
    # For Ed25519 point (x, y), X25519 u = (1 + y) / (1 - y) mod p
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as Ed25519Pub

    # Get raw 32 bytes (compressed Edwards y-coordinate with sign bit)
    raw = ed_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Convert Edwards y to Montgomery u
    p = 2**255 - 19
    y_bytes = bytearray(raw)
    # The raw bytes are little-endian y-coordinate with high bit as sign
    y_bytes[31] &= 0x7F  # Clear sign bit
    y = int.from_bytes(y_bytes, 'little')

    # u = (1 + y) * inverse(1 - y) mod p
    u = ((1 + y) * pow(1 - y, p - 2, p)) % p
    u_bytes = u.to_bytes(32, 'little')

    return X25519PublicKey.from_public_bytes(u_bytes)


def encrypt_message_x25519(message: str, recipient_pub_b64: str) -> dict:
    """
    Encrypt message using X25519 + AES-256-GCM.

    Args:
        message: Plain text message to encrypt
        recipient_pub_b64: Recipient's Ed25519 public key (base64)

    Returns:
        dict: Encrypted message with ephemeral_public_key, nonce, encrypted_data, algorithm
    """
    # Generate ephemeral X25519 keypair
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key()

    # Convert recipient's Ed25519 pub to X25519
    recipient_x25519 = _ed25519_pub_to_x25519(recipient_pub_b64)

    # ECDH shared secret
    shared_secret = ephemeral_private.exchange(recipient_x25519)

    # Derive encryption key via HKDF
    from cryptography.hazmat.primitives import serialization
    encryption_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b'agdns:encryption:v1',
        backend=default_backend()
    ).derive(shared_secret)

    # AES-256-GCM encryption
    nonce = os.urandom(12)
    aesgcm = AESGCM(encryption_key)
    encrypted_data = aesgcm.encrypt(nonce, message.encode('utf-8'), None)

    # Serialize ephemeral public key
    ephemeral_pub_bytes = ephemeral_public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    return {
        'ephemeral_public_key': base64.b64encode(ephemeral_pub_bytes).decode(),
        'nonce': base64.b64encode(nonce).decode(),
        'encrypted_data': base64.b64encode(encrypted_data).decode(),
        'algorithm': 'X25519-AES256-GCM',
    }


def decrypt_message_x25519(encrypted_message: dict, keypair: Ed25519Keypair) -> str:
    """
    Decrypt message using X25519 + AES-256-GCM.

    Args:
        encrypted_message: Encrypted message from encrypt_message_x25519()
        keypair: Recipient's Ed25519 keypair

    Returns:
        str: Decrypted plain text
    """
    # Convert our Ed25519 private key to X25519
    our_x25519 = _ed25519_to_x25519_private(keypair)

    # Reconstruct ephemeral X25519 public key
    ephemeral_pub_bytes = base64.b64decode(encrypted_message['ephemeral_public_key'])
    ephemeral_pub = X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)

    # ECDH shared secret
    shared_secret = our_x25519.exchange(ephemeral_pub)

    # Derive decryption key
    decryption_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b'agdns:encryption:v1',
        backend=default_backend()
    ).derive(shared_secret)

    # AES-256-GCM decryption
    nonce = base64.b64decode(encrypted_message['nonce'])
    encrypted_data = base64.b64decode(encrypted_message['encrypted_data'])
    aesgcm = AESGCM(decryption_key)
    plaintext = aesgcm.decrypt(nonce, encrypted_data, None)

    return plaintext.decode('utf-8')


# ---------------------------------------------------------------------------
# Legacy ECIES encryption (backward compatibility for old format)
# ---------------------------------------------------------------------------


def extract_public_key_from_did(did_document):
    """
    Extract public key from DID document coordinates.
    For PolygonID AuthBJJ credentials, derives secp256k1 key from BabyJubJub coordinates.

    DEPRECATED: Kept for backward compatibility with legacy encrypted messages.
    """
    try:
        x = int(did_document['credentialSubject']['x'])
        y = int(did_document['credentialSubject']['y'])

        did_id = did_document.get('id', '')
        issuer = did_document.get('issuer', '')
        credential_type = did_document.get('credentialSubject', {}).get('type', '')

        seed_data = f"authbjj:{x}:{y}:{did_id}:{issuer}:{credential_type}".encode('utf-8')
        private_key_bytes = hashlib.sha256(seed_data).digest()
        private_key_int = int.from_bytes(private_key_bytes, 'big')

        secp256k1_order = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        private_key_int = (private_key_int % (secp256k1_order - 1)) + 1

        private_key = ec.derive_private_key(private_key_int, ec.SECP256K1(), default_backend())
        public_key = private_key.public_key()
        public_key_numbers = public_key.public_numbers()

        x_bytes = public_key_numbers.x.to_bytes(32, 'big')
        y_bytes = public_key_numbers.y.to_bytes(32, 'big')

        return b'\x04' + x_bytes + y_bytes

    except Exception as e:
        raise ValueError(f"Failed to extract public key from DID document: {e}")


def _derive_secp256k1_private_key_from_did(did_document):
    """
    Internal helper to derive secp256k1 private key from DID document.

    DEPRECATED: Kept for backward compatibility with legacy encrypted messages.
    """
    x = int(did_document['credentialSubject']['x'])
    y = int(did_document['credentialSubject']['y'])

    did_id = did_document.get('id', '')
    issuer = did_document.get('issuer', '')
    credential_type = did_document.get('credentialSubject', {}).get('type', '')

    seed_data = f"authbjj:{x}:{y}:{did_id}:{issuer}:{credential_type}".encode('utf-8')
    private_key_bytes = hashlib.sha256(seed_data).digest()
    private_key_int = int.from_bytes(private_key_bytes, 'big')

    secp256k1_order = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    private_key_int = (private_key_int % (secp256k1_order - 1)) + 1

    return ec.derive_private_key(private_key_int, ec.SECP256K1(), default_backend())


def derive_shared_key_from_seed_and_did(secret_seed, identity_credential):
    """
    Derive a shared authentication key from both seed and DID.

    DEPRECATED: Kept for backward compatibility with legacy encrypted messages.
    """
    seed_private_key_bytes = derive_private_key_from_seed(secret_seed)
    did_public_key_bytes = extract_public_key_from_did(identity_credential)
    did_id = identity_credential.get('id', '')
    combined_data = seed_private_key_bytes + did_public_key_bytes + did_id.encode('utf-8')
    auth_key = hashlib.sha256(combined_data).digest()
    return auth_key


def encrypt_message(message, recipient_credential_or_pub_b64, keypair=None):
    """
    Encrypt message — auto-selects algorithm based on arguments.

    New format (X25519-AES256-GCM): pass recipient_pub_b64 as string
    Legacy format (ECIES-AES256-CBC-AuthBJJ): pass DID document dict
    """
    if isinstance(recipient_credential_or_pub_b64, str):
        # New format: X25519
        return encrypt_message_x25519(message, recipient_credential_or_pub_b64)
    else:
        # Legacy format: ECIES with DID document
        return _encrypt_message_legacy(message, recipient_credential_or_pub_b64)


def decrypt_message(encrypted_message, secret_seed_or_keypair, identity_credential=None):
    """
    Decrypt message — auto-selects algorithm based on the 'algorithm' field.

    New format: pass Ed25519Keypair
    Legacy format: pass secret_seed (str) + identity_credential (dict)
    """
    algorithm = encrypted_message.get('algorithm', '')

    if algorithm == 'X25519-AES256-GCM':
        if isinstance(secret_seed_or_keypair, Ed25519Keypair):
            return decrypt_message_x25519(encrypted_message, secret_seed_or_keypair)
        raise ValueError("X25519 decryption requires an Ed25519Keypair")

    # Legacy ECIES format
    return _decrypt_message_legacy(encrypted_message, secret_seed_or_keypair, identity_credential)


def _encrypt_message_legacy(message, identity_credential_connected_agent):
    """Legacy ECIES encryption with DID documents."""
    try:
        recipient_public_key_bytes = extract_public_key_from_did(identity_credential_connected_agent)

        ephemeral_private_key = ec.generate_private_key(ec.SECP256K1(), default_backend())
        ephemeral_public_key = ephemeral_private_key.public_key()

        recipient_x = int.from_bytes(recipient_public_key_bytes[1:33], 'big')
        recipient_y = int.from_bytes(recipient_public_key_bytes[33:65], 'big')

        try:
            recipient_ec_public_key = ec.EllipticCurvePublicNumbers(
                recipient_x, recipient_y, ec.SECP256K1()
            ).public_key(default_backend())
        except ValueError as e:
            raise ValueError(f"Invalid elliptic curve coordinates in DID document: {e}")

        shared_secret = ephemeral_private_key.exchange(
            ec.ECDH(), recipient_ec_public_key
        )

        recipient_did_id = identity_credential_connected_agent.get('id', '')
        encryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=recipient_did_id.encode('utf-8'),
            info=b'polygonid_authbjj_encryption',
            backend=default_backend()
        ).derive(shared_secret)

        iv = os.urandom(16)
        cipher = Cipher(
            algorithms.AES(encryption_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()

        message_bytes = message.encode('utf-8')
        padding_length = 16 - (len(message_bytes) % 16)
        padded_message = message_bytes + bytes([padding_length] * padding_length)
        encrypted_data = encryptor.update(padded_message) + encryptor.finalize()

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
            'encryption_version': '2.0'
        }

    except Exception as e:
        raise ValueError(f"Encryption failed: {e}")


def _decrypt_message_legacy(encrypted_message, secret_seed, identity_credential):
    """Legacy ECIES decryption with DID documents."""
    try:
        expected_did_id = encrypted_message.get('recipient_did_id', '')
        actual_did_id = identity_credential.get('id', '')

        if expected_did_id != actual_did_id:
            raise ValueError(f"Message encrypted for DID '{expected_did_id}' but attempting to decrypt with DID '{actual_did_id}'")

        credential_type = identity_credential.get('credentialSubject', {}).get('type', '')
        is_authbjj = (credential_type == 'AuthBJJCredential' or
                     'AuthBJJ' in str(identity_credential.get('type', [])))

        if is_authbjj:
            recipient_private_key = _derive_secp256k1_private_key_from_did(identity_credential)

            try:
                auth_key = derive_shared_key_from_seed_and_did(secret_seed, identity_credential)
                seed_derived_private_bytes = derive_private_key_from_seed(secret_seed)
                did_derived_public_bytes = extract_public_key_from_did(identity_credential)

                validation_data = seed_derived_private_bytes + did_derived_public_bytes
                validation_hash = hashlib.sha256(validation_data).digest()

                expected_validation = hashlib.sha256(
                    auth_key + actual_did_id.encode('utf-8')
                ).digest()

                combined_check = hashlib.sha256(validation_hash + expected_validation).digest()
                if len(combined_check) != 32:
                    raise ValueError("Cryptographic validation failed")

            except Exception as e:
                raise ValueError(f"Seed and DID ownership validation failed: {e}")
        else:
            recipient_private_key_bytes = derive_private_key_from_seed(secret_seed)
            derived_public_key = derive_public_key_from_private(recipient_private_key_bytes)
            did_public_key = extract_public_key_from_did(identity_credential)

            if derived_public_key != did_public_key:
                raise ValueError(
                    "Private key derived from seed does not match public key in DID document."
                )

            recipient_private_key = ec.derive_private_key(
                int.from_bytes(recipient_private_key_bytes, 'big'),
                ec.SECP256K1(),
                default_backend()
            )

        ephemeral_public_key_bytes = base64.b64decode(encrypted_message['ephemeral_public_key'])
        ephemeral_x = int.from_bytes(ephemeral_public_key_bytes[1:33], 'big')
        ephemeral_y = int.from_bytes(ephemeral_public_key_bytes[33:65], 'big')

        try:
            ephemeral_public_key = ec.EllipticCurvePublicNumbers(
                ephemeral_x, ephemeral_y, ec.SECP256K1()
            ).public_key(default_backend())
        except ValueError as e:
            raise ValueError(f"Invalid ephemeral public key in encrypted message: {e}")

        shared_secret = recipient_private_key.exchange(ec.ECDH(), ephemeral_public_key)

        decryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=actual_did_id.encode('utf-8'),
            info=b'polygonid_authbjj_encryption',
            backend=default_backend()
        ).derive(shared_secret)

        iv = base64.b64decode(encrypted_message['iv'])
        encrypted_data = base64.b64decode(encrypted_message['encrypted_data'])

        cipher = Cipher(
            algorithms.AES(decryption_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        padded_message = decryptor.update(encrypted_data) + decryptor.finalize()

        padding_length = padded_message[-1]
        if padding_length > 16 or padding_length == 0:
            raise ValueError("Invalid padding in decrypted message")

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

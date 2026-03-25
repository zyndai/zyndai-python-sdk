"""zynd auth login — Onboard via restricted registry (browser-based KYC flow)."""

import argparse
import base64
import hashlib
import secrets
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zyndai_agent.ed25519_identity import keypair_from_private_bytes, save_keypair
from zynd_cli.config import ensure_zynd_dir, developer_key_path, save_config, get_registry_url


def register_parser(subparsers: argparse._SubParsersAction):
    auth_parser = subparsers.add_parser("auth", help="Authentication commands")
    auth_sub = auth_parser.add_subparsers(dest="auth_command")

    login_parser = auth_sub.add_parser("login", help="Register via browser-based onboarding")
    login_parser.add_argument("--registry", dest="auth_registry", required=True, help="Registry URL")
    login_parser.add_argument("--name", default="", help="Developer display name (optional for re-login)")
    login_parser.add_argument("--force", action="store_true", help="Overwrite existing developer keypair")
    login_parser.set_defaults(func=run_login)


def run_login(args: argparse.Namespace):
    ensure_zynd_dir()
    key_path = developer_key_path()

    if key_path.exists() and not args.force:
        print(f"Developer keypair already exists at {key_path}")
        print("Use --force to overwrite.")
        return

    # Use auth-specific --registry flag, fall back to global --registry, then config
    registry_url = args.auth_registry or get_registry_url(getattr(args, "registry", None))

    # Step 1: Get registry info to find auth_url
    print(f"Contacting registry at {registry_url}...")
    try:
        resp = requests.get(f"{registry_url}/v1/info", timeout=10)
        resp.raise_for_status()
        info = resp.json()
    except Exception as e:
        print(f"Failed to reach registry: {e}", file=sys.stderr)
        sys.exit(1)

    onboarding = info.get("developer_onboarding", {})
    mode = onboarding.get("mode", "open")
    auth_url = onboarding.get("auth_url", "")

    if mode != "restricted" or not auth_url:
        print("This registry uses open onboarding. Use 'zynd init' instead.")
        return

    # Step 2: Generate state token
    state = secrets.token_urlsafe(32)

    # Step 3: Start local callback server
    result = {}
    done = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)

            cb_state = params.get("state", [None])[0]
            if cb_state != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch. Please try again.")
                return

            result["developer_id"] = params.get("developer_id", [None])[0]
            result["private_key_enc"] = params.get("private_key_enc", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authentication complete!</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
            done.set()

        def log_message(self, format, *log_args):
            pass  # suppress server logs

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    callback_port = server.server_address[1]

    # Step 4: Open browser to the onboarding page
    query_params = {
        "callback_port": callback_port,
        "state": state,
        "registry_url": registry_url,
    }
    if args.name:
        query_params["name"] = args.name
    query = urlencode(query_params)
    browser_url = f"{auth_url}?{query}"

    print("Opening browser for authentication...")
    print(f"  {browser_url}")
    webbrowser.open(browser_url)

    # Step 5: Wait for callback (5 minute timeout)
    print("Waiting for authentication to complete...")
    server.timeout = 300
    while not done.is_set():
        server.handle_request()

    server.server_close()

    if not result.get("developer_id") or not result.get("private_key_enc"):
        print("Error: incomplete callback data received.", file=sys.stderr)
        sys.exit(1)

    # Step 6: Decrypt private key — AES-256-GCM with SHA256(state)
    try:
        private_key_b64 = _decrypt_private_key(result["private_key_enc"], state)
    except Exception as e:
        print(f"Failed to decrypt private key: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 7: Save keypair
    # The registry returns a full 64-byte Ed25519 private key (seed || public).
    # Python's from_private_bytes expects the 32-byte seed only.
    private_bytes = base64.b64decode(private_key_b64)
    if len(private_bytes) == 64:
        private_bytes = private_bytes[:32]
    kp = keypair_from_private_bytes(private_bytes)
    save_keypair(kp, str(key_path))

    save_config({"registry_url": registry_url})

    print()
    print("Authenticated successfully!")
    print(f"  Developer ID: {result['developer_id']}")
    print(f"  Public key:   {kp.public_key_string}")
    print(f"  Saved to:     {key_path}")
    print()
    print("You can now register agents with: zynd register")


def _decrypt_private_key(ciphertext_b64: str, state: str) -> str:
    """
    Decrypt AES-256-GCM encrypted private key using SHA256(state).
    Compatible with Go's EncryptPrivateKey in agent-dns/internal/models/onboarding.go.

    Format: base64(nonce[12] || ciphertext+tag)
    """
    key = hashlib.sha256(state.encode()).digest()
    raw = base64.b64decode(ciphertext_b64)

    nonce_size = 12
    if len(raw) < nonce_size:
        raise ValueError("ciphertext too short")

    nonce = raw[:nonce_size]
    ciphertext = raw[nonce_size:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode()

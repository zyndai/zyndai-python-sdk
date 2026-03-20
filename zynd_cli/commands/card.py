"""zynd card — Agent Card management subcommands."""

import argparse
import json
import os
import sys

from zyndai_agent.dns_registry import get_agent_card
from zyndai_agent.ed25519_identity import (
    load_keypair,
    save_keypair,
    derive_agent_keypair,
)
from zynd_cli.config import (
    get_registry_url,
    developer_key_path,
    agents_dir,
    ensure_zynd_dir,
)


def register_parser(subparsers: argparse._SubParsersAction):
    p = subparsers.add_parser("card", help="Agent Card management")
    sub = p.add_subparsers(dest="card_action")

    # zynd card init
    init_p = sub.add_parser("init", help="Set up agent keypair and .env for a new agent")
    init_p.add_argument("--index", type=int, default=None, help="Derivation index for agent keypair (default: next available)")

    # zynd card show <agent_id>  (fetch from registry)
    show_p = sub.add_parser("show", help="Show an agent's Agent Card")
    show_p.add_argument("agent_id", nargs="?", help="Agent ID (agdns:...)")
    show_p.add_argument("--file", help="Path to a local .well-known/agent.json file")
    show_p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")

    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    action = getattr(args, "card_action", None)
    if action is None:
        print("Usage: zynd card {init,show}")
        return

    if action == "init":
        _card_init(args)
    elif action == "show":
        _card_show(args)


def _card_init(args: argparse.Namespace):
    """Set up agent keypair and add ZYND_AGENT_KEYPAIR_PATH to .env.

    The .well-known/agent.json card file is NOT created here — it is
    auto-generated at runtime when the Python agent starts, from the
    AgentConfig fields.
    """
    keypair_path = _setup_agent_keypair(args)
    if not keypair_path:
        return

    _add_to_dotenv("ZYND_AGENT_KEYPAIR_PATH", str(keypair_path))

    print(f"\nAgent initialized:")
    print(f"  Keypair: {keypair_path}")
    print(f"  Added ZYND_AGENT_KEYPAIR_PATH to .env")
    print(f"\n  .well-known/agent.json will be auto-generated when the agent runs.")
    print(f"\n  Example usage in Python:")
    print(f"    agent = ZyndAIAgent(AgentConfig(")
    print(f"        name=\"my_agent\",")
    print(f"        description=\"...\",")
    print(f"        capabilities={{...}},")
    print(f"    ))")


def _setup_agent_keypair(args: argparse.Namespace):
    """Derive or find an agent keypair, return the path."""
    dev_key = developer_key_path()
    if not dev_key.exists():
        print("Error: No developer keypair found. Run 'zynd init' first.", file=sys.stderr)
        sys.exit(1)

    ensure_zynd_dir()
    dev_kp = load_keypair(str(dev_key))

    # Determine derivation index
    index = getattr(args, "index", None)
    if index is None:
        # Find next available index
        d = agents_dir()
        index = 0
        while (d / f"agent-{index}.json").exists():
            index += 1

    kp_path = agents_dir() / f"agent-{index}.json"

    if kp_path.exists():
        # Already exists, reuse it
        kp = load_keypair(str(kp_path))
        print(f"Using existing keypair: {kp_path}")
        print(f"  Agent ID:   {kp.agent_id}")
        print(f"  Public key: {kp.public_key_string}")
        return kp_path

    # Derive new keypair
    kp = derive_agent_keypair(dev_kp.private_key, index)
    save_keypair(kp, str(kp_path), derivation_metadata={
        "developer_public_key": dev_kp.public_key_b64,
        "index": index,
    })

    print(f"Derived keypair at index {index}: {kp_path}")
    print(f"  Agent ID:   {kp.agent_id}")
    print(f"  Public key: {kp.public_key_string}")
    return kp_path


def _add_to_dotenv(key: str, value: str, dotenv_path: str = ".env"):
    """Add or update a key in .env file. Creates the file if it doesn't exist."""
    lines = []
    key_found = False

    if os.path.exists(dotenv_path):
        with open(dotenv_path, "r") as f:
            lines = f.readlines()

        # Check if key already exists
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
                lines[i] = f"{key}={value}\n"
                key_found = True
                break

    if not key_found:
        # Add newline before if file exists and doesn't end with one
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")

    with open(dotenv_path, "w") as f:
        f.writelines(lines)


def _card_show(args: argparse.Namespace):
    """Show an agent card from registry or local file."""
    if args.file:
        # Load from local file
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r") as f:
            card = json.load(f)
    elif args.agent_id:
        # Fetch from registry
        registry_url = get_registry_url(getattr(args, "registry", None))
        card = get_agent_card(registry_url, args.agent_id)
        if card is None:
            print(f"Agent card not found: {args.agent_id}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Error: Provide an agent_id or --file path", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "output_json", False):
        print(json.dumps(card, indent=2))
        return

    print(f"Agent Card: {card.get('name', '?')}")
    print(f"  ID:          {card.get('agent_id', '?')}")
    print(f"  Description: {card.get('description', '?')}")
    print(f"  URL:         {card.get('agent_url', '?')}")
    print(f"  Version:     {card.get('version', '?')}")
    print(f"  Status:      {card.get('status', '?')}")
    print(f"  Public key:  {card.get('public_key', '?')}")

    endpoints = card.get("endpoints", {})
    if endpoints:
        print("  Endpoints:")
        for k, v in endpoints.items():
            print(f"    {k}: {v}")

    caps = card.get("capabilities", [])
    if caps:
        print("  Capabilities:")
        for cap in caps:
            print(f"    [{cap.get('category', '?')}] {cap.get('name', '?')}")

    pricing = card.get("pricing")
    if pricing:
        print(f"  Pricing:     {pricing.get('model', '?')} — {pricing.get('currency', '?')}")

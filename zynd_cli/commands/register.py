"""zynd register — Register an agent on the registry."""

import argparse
import json
import os
import sys

from zyndai_agent.ed25519_identity import (
    load_keypair,
    derive_agent_keypair,
    create_derivation_proof,
    save_keypair,
)
from zyndai_agent.dns_registry import register_agent
from zyndai_agent.agent_card_loader import load_agent_card, load_derivation_metadata
from zynd_cli.config import (
    developer_key_path,
    agents_dir,
    get_registry_url,
    ensure_zynd_dir,
)


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("register", help="Register an agent on the registry", parents=parents or [])
    p.add_argument("--name", help="Agent display name")
    p.add_argument("--agent-url", help="Agent base URL")
    p.add_argument("--category", default="general", help="Agent category (default: general)")
    p.add_argument("--tags", nargs="*", help="Agent tags")
    p.add_argument("--summary", help="Agent summary")
    p.add_argument("--index", type=int, help="Derive agent key from developer key at this index")
    p.add_argument("--keypair", help="Path to agent keypair JSON (instead of deriving)")
    p.add_argument("--card", help="Path to .well-known/agent.json card file")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    ensure_zynd_dir()
    dev_key = developer_key_path()

    # Card-based registration
    if args.card:
        _register_from_card(args)
        return

    # Require --name and --agent-url for non-card registration
    if not args.name:
        print("Error: --name is required (or use --card)", file=sys.stderr)
        sys.exit(1)
    if not args.agent_url:
        print("Error: --agent-url is required (or use --card)", file=sys.stderr)
        sys.exit(1)

    if args.keypair:
        kp = load_keypair(args.keypair)
        developer_proof = None
        developer_id = None
    elif args.index is not None:
        if not dev_key.exists():
            print("Error: No developer keypair found. Run 'zynd init' first.", file=sys.stderr)
            sys.exit(1)
        dev_kp = load_keypair(str(dev_key))
        kp = derive_agent_keypair(dev_kp.private_key, args.index)
        developer_proof = create_derivation_proof(dev_kp, kp.public_key, args.index)
        developer_id = dev_kp.agent_id

        # Save derived agent keypair
        agent_file = agents_dir() / f"agent-{args.index}.json"
        save_keypair(kp, str(agent_file))
    else:
        # Default: derive at next available index
        if not dev_key.exists():
            print("Error: No developer keypair found. Run 'zynd init' first.", file=sys.stderr)
            sys.exit(1)
        dev_kp = load_keypair(str(dev_key))
        index = _next_agent_index()
        kp = derive_agent_keypair(dev_kp.private_key, index)
        developer_proof = create_derivation_proof(dev_kp, kp.public_key, index)
        developer_id = dev_kp.agent_id

        agent_file = agents_dir() / f"agent-{index}.json"
        save_keypair(kp, str(agent_file))

    registry_url = get_registry_url(getattr(args, "registry", None))

    try:
        agent_id = register_agent(
            registry_url=registry_url,
            keypair=kp,
            name=args.name,
            agent_url=args.agent_url,
            category=args.category,
            tags=args.tags,
            summary=args.summary,
            developer_id=developer_id,
            developer_proof=developer_proof,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps({"agent_id": agent_id, "public_key": kp.public_key_string}))
    else:
        print(f"Agent registered successfully!")
        print(f"  Agent ID:   {agent_id}")
        print(f"  Public key: {kp.public_key_string}")
        print(f"  Registry:   {registry_url}")


def _next_agent_index() -> int:
    """Find the next available agent index in ~/.zynd/agents/."""
    d = agents_dir()
    index = 0
    while (d / f"agent-{index}.json").exists():
        index += 1
    return index


def _register_from_card(args: argparse.Namespace):
    """Register an agent from a .well-known/agent.json card file + keypair."""
    card = load_agent_card(args.card)

    # Resolve keypair
    keypair_path = args.keypair or os.environ.get("ZYND_AGENT_KEYPAIR_PATH")
    if not keypair_path:
        print("Error: --keypair or ZYND_AGENT_KEYPAIR_PATH is required with --card", file=sys.stderr)
        sys.exit(1)

    kp = load_keypair(os.path.expanduser(keypair_path))

    # Check for derivation metadata for developer proof
    developer_proof = None
    developer_id = None
    derivation = load_derivation_metadata(keypair_path)
    if derivation:
        dev_key = developer_key_path()
        if dev_key.exists():
            dev_kp = load_keypair(str(dev_key))
            developer_proof = create_derivation_proof(dev_kp, kp.public_key, derivation["index"])
            developer_id = dev_kp.agent_id

    # Get agent URL from args or card
    agent_url = args.agent_url
    if not agent_url:
        server = card.get("server", {})
        host = server.get("host", "localhost")
        if host == "0.0.0.0":
            host = "localhost"
        port = server.get("port", 5000)
        scheme = "https" if port == 443 else "http"
        agent_url = f"{scheme}://{host}:{port}"

    registry_url = get_registry_url(getattr(args, "registry", None))

    # Override from card's registry section
    card_registry = card.get("registry", {})
    if card_registry.get("url"):
        registry_url = card_registry["url"]

    try:
        agent_id = register_agent(
            registry_url=registry_url,
            keypair=kp,
            name=card.get("name", ""),
            agent_url=agent_url,
            category=args.category or card.get("category", "general"),
            tags=args.tags or card.get("tags"),
            summary=args.summary or card.get("summary"),
            developer_id=developer_id,
            developer_proof=developer_proof,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps({"agent_id": agent_id, "public_key": kp.public_key_string}))
    else:
        print(f"Agent registered successfully from card!")
        print(f"  Agent ID:   {agent_id}")
        print(f"  Name:       {card.get('name', '?')}")
        print(f"  Public key: {kp.public_key_string}")
        print(f"  Registry:   {registry_url}")

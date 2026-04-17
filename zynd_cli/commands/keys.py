"""zynd keys — Keypair management subcommands."""

import argparse
import json
import sys

from zyndai_agent.ed25519_identity import (
    generate_keypair,
    load_keypair,
    load_keypair_with_metadata,
    save_keypair,
    derive_agent_keypair,
)
from zynd_cli.config import (
    developer_key_path,
    agents_dir,
    ensure_zynd_dir,
)


def register_parser(subparsers: argparse._SubParsersAction):
    p = subparsers.add_parser("keys", help="Manage keypairs")
    sub = p.add_subparsers(dest="keys_action")

    # zynd keys list
    sub.add_parser("list", help="List all keypairs")

    # zynd keys create
    create_p = sub.add_parser("create", help="Create a new standalone agent keypair")
    create_p.add_argument("--name", default=None, help="Keypair filename (default: agent-N)")

    # zynd keys derive
    derive_p = sub.add_parser("derive", help="Derive agent keypair from developer key")
    derive_p.add_argument("--index", type=int, required=True, help="Derivation index")

    # zynd keys show
    show_p = sub.add_parser("show", help="Show keypair details")
    show_p.add_argument("name", help="Keypair name (e.g. 'developer', 'agent-0')")

    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    action = getattr(args, "keys_action", None)
    if action is None:
        print("Usage: zynd keys {list,create,derive,show}")
        return

    if action == "list":
        _list_keys()
    elif action == "create":
        _create_key(args)
    elif action == "derive":
        _derive_key(args)
    elif action == "show":
        _show_key(args)


def _list_keys():
    """List all keypairs in ~/.zynd/."""
    dev = developer_key_path()
    found = False

    if dev.exists():
        kp = load_keypair(str(dev))
        print(f"  developer")
        print(f"    Agent ID:   {kp.entity_id}")
        print(f"    Public key: {kp.public_key_string}")
        print()
        found = True

    d = agents_dir()
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.suffix == ".json":
                try:
                    kp = load_keypair(str(f))
                    name = f.stem
                    print(f"  {name}")
                    print(f"    Agent ID:   {kp.entity_id}")
                    print(f"    Public key: {kp.public_key_string}")
                    print()
                    found = True
                except Exception:
                    continue

    if not found:
        print("No keypairs found. Run 'zynd init' to create a developer keypair.")


def _create_key(args: argparse.Namespace):
    ensure_zynd_dir()
    kp = generate_keypair()

    if args.name:
        filename = f"{args.name}.json"
    else:
        # Find next available agent-N
        d = agents_dir()
        idx = 0
        while (d / f"agent-{idx}.json").exists():
            idx += 1
        filename = f"agent-{idx}.json"

    path = agents_dir() / filename
    save_keypair(kp, str(path))

    print(f"Keypair created: {path}")
    print(f"  Agent ID:   {kp.entity_id}")
    print(f"  Public key: {kp.public_key_string}")


def _derive_key(args: argparse.Namespace):
    dev = developer_key_path()
    if not dev.exists():
        print("Error: No developer keypair found. Run 'zynd init' first.", file=sys.stderr)
        sys.exit(1)

    ensure_zynd_dir()
    dev_kp = load_keypair(str(dev))
    kp = derive_agent_keypair(dev_kp.private_key, args.index)

    path = agents_dir() / f"agent-{args.index}.json"
    save_keypair(kp, str(path), derivation_metadata={
        "developer_public_key": dev_kp.public_key_b64,
        "index": args.index,
    })

    print(f"Derived keypair at index {args.index}: {path}")
    print(f"  Agent ID:   {kp.entity_id}")
    print(f"  Public key: {kp.public_key_string}")


def _show_key(args: argparse.Namespace):
    name = args.name

    if name == "developer":
        path = developer_key_path()
    else:
        path = agents_dir() / f"{name}.json"

    if not path.exists():
        print(f"Keypair not found: {path}", file=sys.stderr)
        sys.exit(1)

    kp, metadata = load_keypair_with_metadata(str(path))
    print(f"Keypair: {name}")
    print(f"  File:       {path}")
    print(f"  Agent ID:   {kp.entity_id}")
    print(f"  Public key: {kp.public_key_string}")
    if metadata:
        print(f"  Derived from:")
        print(f"    Developer: ed25519:{metadata['developer_public_key']}")
        print(f"    Index:     {metadata['index']}")
    print(f"\n  To use this keypair:")
    print(f"    export ZYND_AGENT_KEYPAIR_PATH={path}")

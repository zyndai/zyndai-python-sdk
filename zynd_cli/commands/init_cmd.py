"""zynd init — Generate developer keypair."""

import argparse
import json

from zyndai_agent.ed25519_identity import generate_keypair, save_keypair
from zynd_cli.config import ensure_zynd_dir, developer_key_path, save_config, get_registry_url


def register_parser(subparsers: argparse._SubParsersAction):
    p = subparsers.add_parser("init", help="Initialize developer identity (generate keypair)")
    p.add_argument("--force", action="store_true", help="Overwrite existing developer keypair")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    ensure_zynd_dir()
    key_path = developer_key_path()

    if key_path.exists() and not args.force:
        print(f"Developer keypair already exists at {key_path}")
        print("Use --force to overwrite.")
        return

    kp = generate_keypair()
    save_keypair(kp, str(key_path))

    # Save default config
    save_config({"registry_url": get_registry_url()})

    print(f"Developer keypair created: {key_path}")
    print(f"  Public key: {kp.public_key_string}")
    print(f"  Agent ID:   {kp.agent_id}")
    print()
    print("You can now register agents with: zynd register")

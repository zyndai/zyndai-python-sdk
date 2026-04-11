"""zynd deregister — Remove an entity from the registry."""

import argparse
import json
import sys

from zyndai_agent.ed25519_identity import load_keypair
from zyndai_agent.dns_registry import delete_agent
from zynd_cli.config import (
    get_registry_url,
    developer_key_path,
    agents_dir,
    services_dir,
)


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser(
        "deregister", help="Remove an entity from the registry", parents=parents or []
    )
    p.add_argument("entity_id", help="Entity ID to deregister (zns:... or zns:svc:...)")
    p.add_argument("--keypair", help="Path to entity keypair JSON")
    p.add_argument(
        "--index",
        type=int,
        help="Entity derivation index (to find keypair in ~/.zynd/agents/)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))

    # Load the entity's keypair for auth
    if args.keypair:
        kp = load_keypair(args.keypair)
    elif args.index is not None:
        agent_file = agents_dir() / f"agent-{args.index}.json"
        if not agent_file.exists():
            print(f"Error: Keypair not found: {agent_file}", file=sys.stderr)
            sys.exit(1)
        kp = load_keypair(str(agent_file))
    else:
        # Try to find keypair by matching entity_id across saved keys
        kp = _find_keypair_for_entity(args.entity_id)
        if kp is None:
            print(
                "Error: Cannot find keypair for this entity. "
                "Use --keypair or --index to specify.",
                file=sys.stderr,
            )
            sys.exit(1)

    success = delete_agent(registry_url, args.entity_id, kp)
    if success:
        print(f"Entity deregistered: {args.entity_id}")
    else:
        print(f"Failed to deregister entity: {args.entity_id}", file=sys.stderr)
        sys.exit(1)


def _find_keypair_for_entity(entity_id: str):
    """Search ~/.zynd/agents/ and ~/.zynd/services/ for a keypair matching the given entity_id."""
    from zyndai_agent.ed25519_identity import load_keypair as lk

    # Search agents directory
    d = agents_dir()
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.suffix == ".json":
                try:
                    kp = lk(str(f))
                    if kp.agent_id == entity_id:
                        return kp
                except Exception:
                    continue

    # Search services directory
    svc_dir = services_dir()
    if svc_dir.exists():
        for f in sorted(svc_dir.rglob("*.json")):
            try:
                kp = lk(str(f))
                if kp.agent_id == entity_id:
                    return kp
            except Exception:
                continue

    # Also check developer key
    dev = developer_key_path()
    if dev.exists():
        try:
            kp = lk(str(dev))
            if kp.agent_id == entity_id:
                return kp
        except Exception:
            pass

    return None

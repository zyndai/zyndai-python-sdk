"""zynd deregister — Remove an agent from the registry."""

import argparse
import json
import sys

from zyndai_agent.ed25519_identity import load_keypair
from zyndai_agent.dns_registry import delete_agent
from zynd_cli.config import get_registry_url, developer_key_path, agents_dir


def register_parser(subparsers: argparse._SubParsersAction):
    p = subparsers.add_parser("deregister", help="Remove an agent from the registry")
    p.add_argument("agent_id", help="Agent ID to deregister (agdns:...)")
    p.add_argument("--keypair", help="Path to agent keypair JSON")
    p.add_argument("--index", type=int, help="Agent derivation index (to find keypair in ~/.zynd/agents/)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))

    # Load the agent's keypair for auth
    if args.keypair:
        kp = load_keypair(args.keypair)
    elif args.index is not None:
        agent_file = agents_dir() / f"agent-{args.index}.json"
        if not agent_file.exists():
            print(f"Error: Agent keypair not found: {agent_file}", file=sys.stderr)
            sys.exit(1)
        kp = load_keypair(str(agent_file))
    else:
        # Try to find keypair by matching agent_id across saved keys
        kp = _find_keypair_for_agent(args.agent_id)
        if kp is None:
            print(
                "Error: Cannot find keypair for this agent. "
                "Use --keypair or --index to specify.",
                file=sys.stderr,
            )
            sys.exit(1)

    success = delete_agent(registry_url, args.agent_id, kp)
    if success:
        print(f"Agent deregistered: {args.agent_id}")
    else:
        print(f"Failed to deregister agent: {args.agent_id}", file=sys.stderr)
        sys.exit(1)


def _find_keypair_for_agent(agent_id: str):
    """Search ~/.zynd/agents/ for a keypair matching the given agent_id."""
    from zyndai_agent.ed25519_identity import load_keypair as lk

    d = agents_dir()
    if not d.exists():
        return None
    for f in sorted(d.iterdir()):
        if f.suffix == ".json":
            try:
                kp = lk(str(f))
                if kp.agent_id == agent_id:
                    return kp
            except Exception:
                continue

    # Also check developer key
    dev = developer_key_path()
    if dev.exists():
        try:
            kp = lk(str(dev))
            if kp.agent_id == agent_id:
                return kp
        except Exception:
            pass

    return None

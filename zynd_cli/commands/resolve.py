"""zynd resolve — Look up an agent by ID."""

import argparse
import json
import sys

from zyndai_agent.dns_registry import get_agent
from zynd_cli.config import get_registry_url


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("resolve", help="Resolve an agent by ID", parents=parents or [])
    p.add_argument("agent_id", help="Agent ID (agdns:...)")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))
    result = get_agent(registry_url, args.agent_id)

    if result is None:
        print(f"Agent not found: {args.agent_id}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps(result, indent=2))
        return

    print(f"Agent: {result.get('name', '?')}")
    print(f"  ID:         {result.get('agent_id', '?')}")
    print(f"  Category:   {result.get('category', '?')}")
    print(f"  Public key: {result.get('public_key', '?')}")
    status = result.get("status")
    if status:
        print(f"  Status:     {status}")
    last_hb = result.get("last_heartbeat")
    if last_hb:
        print(f"  Last heartbeat: {last_hb}")
    tags = result.get("tags", [])
    if tags:
        print(f"  Tags:       {', '.join(tags)}")
    summary = result.get("summary")
    if summary:
        print(f"  Summary:    {summary}")

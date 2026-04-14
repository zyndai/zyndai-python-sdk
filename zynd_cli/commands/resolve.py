"""zynd resolve — Look up an entity by ID."""

import argparse
import json
import sys

from zyndai_agent.dns_registry import get_entity
from zynd_cli.config import get_registry_url


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("resolve", help="Resolve an entity by ID", parents=parents or [])
    p.add_argument("entity_id", help="Entity ID (agdns:...)")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))
    result = get_entity(registry_url, args.entity_id)

    if result is None:
        print(f"Entity not found: {args.entity_id}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps(result, indent=2))
        return

    print(f"Entity: {result.get('name', '?')}")
    print(f"  ID:         {result.get('entity_id', '?')}")
    print(f"  Type:       {result.get('type', '?')}")
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

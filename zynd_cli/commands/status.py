"""zynd status — Check registry node health."""

import argparse
import json
import sys

from zyndai_agent.dns_registry import get_network_status, get_registry_info
from zynd_cli.config import get_registry_url


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("status", help="Check registry node status", parents=parents or [])
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))

    data = get_network_status(registry_url)
    if data is None:
        print(f"Error: Could not reach registry at {registry_url}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        # Include registry info in JSON output
        info = get_registry_info(registry_url)
        if info:
            data["registry_info"] = info
        print(json.dumps(data, indent=2))
        return

    print(f"Registry: {registry_url}")
    print(f"  Status:       {data.get('status', '?')}")
    print(f"  Node ID:      {data.get('node_id', '?')}")
    print(f"  Version:      {data.get('version', '?')}")
    print(f"  Agents:       {data.get('agent_count', '?')}")
    print(f"  Peers:        {data.get('peer_count', '?')}")
    print(f"  Uptime:       {data.get('uptime', '?')}")

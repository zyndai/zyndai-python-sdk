"""zynd status — Check registry node health."""

import argparse
import json
import sys

import requests

from zynd_cli.config import get_registry_url


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("status", help="Check registry node status")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))

    try:
        resp = requests.get(f"{registry_url}/v1/network/status", timeout=10)
    except requests.RequestException as e:
        print(f"Error: Could not reach registry at {registry_url}: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: Registry returned status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()

    if args.output_json:
        print(json.dumps(data, indent=2))
        return

    print(f"Registry: {registry_url}")
    print(f"  Status:       {data.get('status', '?')}")
    print(f"  Node ID:      {data.get('node_id', '?')}")
    print(f"  Version:      {data.get('version', '?')}")
    print(f"  Agents:       {data.get('agent_count', '?')}")
    print(f"  Peers:        {data.get('peer_count', '?')}")
    print(f"  Uptime:       {data.get('uptime', '?')}")

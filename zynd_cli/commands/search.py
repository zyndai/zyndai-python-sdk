"""zynd search — Search for agents on the registry."""

import argparse
import json

from zyndai_agent.dns_registry import search_agents
from zynd_cli.config import get_registry_url


def register_parser(subparsers: argparse._SubParsersAction):
    p = subparsers.add_parser("search", help="Search for agents")
    p.add_argument("query", nargs="?", help="Search query")
    p.add_argument("--category", help="Filter by category")
    p.add_argument("--tags", nargs="*", help="Filter by tags")
    p.add_argument("--max-results", type=int, default=10, help="Max results (default: 10)")
    p.add_argument("--federated", action="store_true", help="Search across federated nodes")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))

    result = search_agents(
        registry_url=registry_url,
        query=args.query,
        category=args.category,
        tags=args.tags,
        max_results=args.max_results,
        federated=args.federated,
    )

    if args.output_json:
        print(json.dumps(result, indent=2))
        return

    agents = result.get("results", [])
    total = result.get("total_found", len(agents))

    if not agents:
        print("No agents found.")
        return

    print(f"Found {total} agent(s):\n")
    for agent in agents:
        agent_id = agent.get("agent_id", "?")
        name = agent.get("name", "?")
        category = agent.get("category", "?")
        url = agent.get("agent_url", "?")
        status = agent.get("status", "")
        status_label = f"  [{status}]" if status else ""
        print(f"  {name}{status_label}")
        print(f"    ID:       {agent_id}")
        print(f"    Category: {category}")
        print(f"    URL:      {url}")
        print()

"""zynd search — Search for agents on the registry."""

import argparse
import json

from zyndai_agent.dns_registry import search_entities
from zynd_cli.config import get_registry_url


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("search", help="Search for agents", parents=parents or [])
    p.add_argument("query", nargs="?", help="Search query")
    p.add_argument("--category", help="Filter by category")
    p.add_argument("--tags", nargs="*", help="Filter by tags")
    p.add_argument("--skills", nargs="*", help="Filter by skills (e.g., code-review)")
    p.add_argument("--protocols", nargs="*", help="Filter by protocols (e.g., a2a, mcp)")
    p.add_argument("--languages", nargs="*", help="Filter by languages (e.g., python)")
    p.add_argument("--models", nargs="*", help="Filter by AI models (e.g., gpt-4)")
    p.add_argument("--status", choices=["active", "inactive", "any"], help="Filter by status")
    p.add_argument("--developer", dest="developer_id", help="Filter by developer ID")
    p.add_argument("--developer-handle", dest="developer_handle", help="Filter by developer handle (e.g., acme-corp)")
    p.add_argument("--fqan", help="Look up agent by exact FQAN (e.g., dns01.zynd.ai/acme-corp/my-agent)")
    p.add_argument("--type", dest="entity_type", choices=["agent", "service", "any"], default="any", help="Filter by type (default: any)")
    p.add_argument("--min-trust", type=float, dest="min_trust_score", help="Minimum trust score (0.0-1.0)")
    p.add_argument("--max-results", type=int, default=10, help="Max results (default: 10)")
    p.add_argument("--offset", type=int, default=0, help="Pagination offset")
    p.add_argument("--federated", action="store_true", help="Search across federated nodes")
    p.add_argument("--enrich", action="store_true", help="Include full Agent Card in results")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    registry_url = get_registry_url(getattr(args, "registry", None))

    result = search_entities(
        registry_url=registry_url,
        query=args.query,
        category=args.category,
        tags=args.tags,
        skills=getattr(args, "skills", None),
        protocols=getattr(args, "protocols", None),
        languages=getattr(args, "languages", None),
        models=getattr(args, "models", None),
        min_trust_score=getattr(args, "min_trust_score", None),
        status=getattr(args, "status", None),
        developer_id=getattr(args, "developer_id", None),
        developer_handle=getattr(args, "developer_handle", None),
        fqan=getattr(args, "fqan", None),
        entity_type=getattr(args, "entity_type", None) if getattr(args, "entity_type", "any") != "any" else None,
        max_results=args.max_results,
        offset=getattr(args, "offset", 0),
        federated=args.federated,
        enrich=getattr(args, "enrich", False),
    )

    if args.output_json:
        print(json.dumps(result, indent=2))
        return

    agents = result.get("results", [])
    total = result.get("total_found", len(agents))
    offset = result.get("offset", 0)
    has_more = result.get("has_more", False)

    if not agents:
        print("No agents found.")
        return

    print(f"Found {total} agent(s):\n")
    for agent in agents:
        entity_id = agent.get("entity_id", "?")
        name = agent.get("name", "?")
        category = agent.get("category", "?")
        status = agent.get("status", "")
        score = agent.get("score", 0)
        summary = agent.get("summary", "")
        dev_id = agent.get("developer_id", "")
        tags = agent.get("tags", [])

        entity_type = agent.get("type", "agent")
        type_label = "[SERVICE]" if entity_type == "service" else "[AGENT]"
        status_label = f"  [{status}]" if status else ""
        print(f"  {type_label} {name}{status_label}")
        print(f"    ID:       {entity_id}")
        print(f"    Category: {category}")
        if tags:
            print(f"    Tags:     {', '.join(tags)}")
        if summary:
            print(f"    Summary:  {summary}")
        fqan = agent.get("fqan", "")
        dev_handle = agent.get("developer_handle", "")
        if fqan:
            print(f"    FQAN:      {fqan}")
        if dev_handle:
            print(f"    Handle:    @{dev_handle}")
        elif dev_id:
            print(f"    Developer: {dev_id}")
        if score:
            print(f"    Score:    {score:.4f}")
        print()

    if has_more:
        next_offset = offset + len(agents)
        print(f"  More results available. Use --offset {next_offset} to see next page.")

    # Show search stats if available
    stats = result.get("search_stats")
    if stats:
        parts = []
        if stats.get("local_results"):
            parts.append(f"local:{stats['local_results']}")
        if stats.get("gossip_results"):
            parts.append(f"gossip:{stats['gossip_results']}")
        if stats.get("federated_results"):
            parts.append(f"federated:{stats['federated_results']}")
        if stats.get("peers_queried"):
            parts.append(f"peers:{stats['peers_queried']}")
        if parts:
            print(f"  [{' | '.join(parts)}]")

"""zynd info — Display developer and agent identity details."""

import argparse
import json
import sys

import requests as _req
from zyndai_agent.ed25519_identity import load_keypair, load_keypair_with_metadata, generate_developer_id
from zynd_cli.config import developer_key_path, agents_dir, get_registry_url


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("info", help="Show developer and agent identity details", parents=parents or [])
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace):
    try:
        from rich.console import Console
        console = Console()
        use_rich = True
    except ImportError:
        console = None
        use_rich = False

    dev_path = developer_key_path()
    registry_url = get_registry_url(getattr(args, "registry", None))

    # Developer info
    dev_info = None
    if dev_path.exists():
        dev_kp = load_keypair(str(dev_path))
        dev_id = generate_developer_id(dev_kp.public_key_bytes)
        dev_info = {
            "developer_id": dev_id,
            "public_key": dev_kp.public_key_string,
            "keypair_path": str(dev_path),
            "handle": "",
        }
        # Fetch developer handle from registry
        try:
            resp = _req.get(f"{registry_url}/v1/developers/{dev_id}", timeout=5)
            if resp.status_code == 200:
                remote_dev = resp.json()
                dev_info["handle"] = remote_dev.get("dev_handle", "")
        except Exception:
            pass

    # Agent info
    agent_infos = []
    a_dir = agents_dir()
    if a_dir.exists():
        for agent_folder in sorted(a_dir.iterdir()):
            kp_file = agent_folder / "keypair.json"
            if not kp_file.exists():
                continue
            try:
                kp, meta = load_keypair_with_metadata(str(kp_file))
                derived = meta.get("derived_from", {})
                agent_infos.append({
                    "name": agent_folder.name,
                    "entity_id": kp.entity_id,
                    "public_key": kp.public_key_string,
                    "keypair_path": str(kp_file),
                    "derivation_index": derived.get("index"),
                    "fqan": "",
                })
            except Exception:
                continue

    # Fetch FQANs for agents from registry
    for a_info in agent_infos:
        try:
            resp = _req.get(f"{registry_url}/v1/entities/{a_info['entity_id']}", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                a_info["fqan"] = data.get("fqan", "")
        except Exception:
            pass

    if args.output_json:
        out = {
            "registry_url": registry_url,
            "developer": dev_info,
            "agents": agent_infos,
        }
        print(json.dumps(out, indent=2))
        return

    if use_rich:
        console.print()
        console.print(f"  [bold #8B5CF6]ZYND Identity[/bold #8B5CF6]")
        console.print(f"  [dim]Registry:[/dim] {registry_url}")
        console.print()

        if dev_info:
            console.print(f"  [bold]Developer[/bold]")
            console.print(f"    [dim]ID:[/dim]         {dev_info['developer_id']}")
            if dev_info.get("handle"):
                console.print(f"    [dim]Handle:[/dim]     [bold #06B6D4]@{dev_info['handle']}[/bold #06B6D4]")
            console.print(f"    [dim]Public Key:[/dim] {dev_info['public_key']}")
            console.print(f"    [dim]Keypair:[/dim]    {dev_info['keypair_path']}")
        else:
            console.print(f"  [yellow]No developer key found.[/yellow] Run 'zynd auth login' first.")

        console.print()
        if agent_infos:
            console.print(f"  [bold]Agents ({len(agent_infos)})[/bold]")
            for a in agent_infos:
                idx = a.get("derivation_index")
                idx_label = f" [dim](index {idx})[/dim]" if idx is not None else ""
                console.print(f"    [bold #06B6D4]{a['name']}[/bold #06B6D4]{idx_label}")
                console.print(f"      [dim]ID:[/dim]   {a['entity_id']}")
                if a.get("fqan"):
                    console.print(f"      [dim]FQAN:[/dim] [bold #8B5CF6]{a['fqan']}[/bold #8B5CF6]")
                console.print(f"      [dim]Key:[/dim]  {a['public_key']}")
        else:
            console.print(f"  [dim]No agents found.[/dim] Run 'zynd agent init' to create one.")
        console.print()
    else:
        print(f"\nZYND Identity")
        print(f"  Registry: {registry_url}\n")

        if dev_info:
            print(f"  Developer")
            print(f"    ID:         {dev_info['developer_id']}")
            if dev_info.get("handle"):
                print(f"    Handle:     @{dev_info['handle']}")
            print(f"    Public Key: {dev_info['public_key']}")
            print(f"    Keypair:    {dev_info['keypair_path']}")
        else:
            print(f"  No developer key found. Run 'zynd auth login' first.")

        print()
        if agent_infos:
            print(f"  Agents ({len(agent_infos)})")
            for a in agent_infos:
                idx = a.get("derivation_index")
                idx_label = f" (index {idx})" if idx is not None else ""
                print(f"    {a['name']}{idx_label}")
                print(f"      ID:   {a['entity_id']}")
                if a.get("fqan"):
                    print(f"      FQAN: {a['fqan']}")
                print(f"      Key:  {a['public_key']}")
        else:
            print(f"  No agents found. Run 'zynd agent init' to create one.")
        print()

"""zynd agent — Agent project scaffolding and unified run."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

from zyndai_agent.ed25519_identity import (
    load_keypair,
    load_keypair_with_metadata,
    save_keypair,
    derive_agent_keypair,
)
from zyndai_agent.dns_registry import check_entity_name_available
from zynd_cli.config import (
    developer_key_path,
    agents_dir,
    agent_dir,
    ensure_zynd_dir,
    get_registry_url,
)
from zynd_cli.commands._entity_base import EntityRunner, slugify_name
from zynd_cli.templates import FRAMEWORKS, FRAMEWORK_ORDER


class AgentRunner(EntityRunner):
    """`zynd agent run` — starts and upserts an agent entity.

    Adds two agent-specific behaviors on top of the base runner:
      * Checks that the ZNS name is available under the developer's
        handle before registering.
      * Includes a SHA-256 ``codebase_hash`` on update so the registry
        can surface drift between deployed versions.
    """

    entity_type = "agent"
    label = "Agent"
    script_name = "agent.py"
    config_name = "agent.config.json"
    keypair_env = "ZYND_AGENT_KEYPAIR_PATH"
    slug_suffix = "-agent"

    def pre_register(
        self, config, kp, registry_url, dev_id, entity_id, console
    ) -> Optional[str]:
        name_zns = super().pre_register(
            config, kp, registry_url, dev_id, entity_id, console
        )
        if not name_zns:
            return None
        try:
            import requests as _req

            dev_resp = _req.get(f"{registry_url}/v1/developers/{dev_id}")
            if dev_resp.status_code == 404:
                console.print(
                    f"  [dim]Warning: Developer not found on registry — "
                    f"name binding skipped[/dim]"
                )
                return None
            if dev_resp.status_code != 200:
                return name_zns
            dev_handle = dev_resp.json().get("dev_handle", "")
            if not dev_handle:
                console.print(
                    f"  [dim]Warning: No developer handle — name binding "
                    f"skipped[/dim]"
                )
                return None
            avail = check_entity_name_available(
                registry_url, dev_handle, name_zns
            )
            if not avail.get("available", True):
                existing_id = avail.get("existing_entity_id", "")
                if existing_id and existing_id != entity_id:
                    console.print(
                        f"  [bold red]✗[/bold red] Name '{name_zns}' "
                        f"already taken by {existing_id}"
                    )
                    sys.exit(1)
            else:
                console.print(
                    f"  [bold #8B5CF6]✓[/bold #8B5CF6] Name "
                    f"'{name_zns}' is available"
                )
        except SystemExit:
            raise
        except Exception as e:
            console.print(
                f"  [dim]Warning: Could not check name: {e}[/dim]"
            )
        return name_zns

    def build_update_body(self, config, entity_url):
        body = super().build_update_body(config, entity_url)
        body["entity_url"] = entity_url
        body["codebase_hash"] = _compute_codebase_hash(".")
        return body


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("agent", help="Agent project management", parents=parents or [])
    sub = p.add_subparsers(dest="agent_command")

    # zynd agent init
    init_p = sub.add_parser("init", help="Create a new agent project")
    init_p.add_argument("--name", help="Agent name (skips interactive prompt)")
    init_p.add_argument("--framework", choices=list(FRAMEWORKS.keys()), help="Framework (skips interactive prompt)")
    init_p.add_argument("--index", type=int, default=None, help="Derivation index (default: next available)")
    init_p.set_defaults(func=_agent_init)

    # zynd agent run
    run_p = sub.add_parser(
        "run",
        help="Start the agent, register/update it on the network, and keep running",
    )
    run_p.add_argument("--config", default="agent.config.json", help="Path to agent.config.json")
    run_p.add_argument("--entity-url", help="Override entity URL for registration")
    run_p.add_argument("--port", type=int, help="Override webhook port")
    run_p.set_defaults(func=_agent_run)

    p.set_defaults(func=_agent_help)


def _agent_help(args: argparse.Namespace):
    print("Usage: zynd agent {init,run}")
    print()
    print("Commands:")
    print("  init   Create a new agent project (interactive wizard)")
    print("  run    Start the agent, register/update it on the network, and run")


def _agent_init(args: argparse.Namespace):
    """Interactive wizard to scaffold a new agent project."""

    # 1. Check developer key
    dev_key = developer_key_path()
    if not dev_key.exists():
        print("Error: No developer keypair found.", file=sys.stderr)
        print("Run 'zynd auth login --registry <url>' first.")
        sys.exit(1)

    # 2. Select framework
    framework = getattr(args, "framework", None)
    if not framework:
        from zynd_cli.tui import select as tui_select

        options = [
            {"label": fw["label"], "description": fw["description"]}
            for fw in [FRAMEWORKS[k] for k in FRAMEWORK_ORDER]
        ]
        idx = tui_select("Select a framework", options)
        framework = FRAMEWORK_ORDER[idx]

    fw_info = FRAMEWORKS[framework]

    from rich.console import Console
    console = Console()
    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Framework: [bold]{fw_info['label']}[/bold]")

    # 3. Get agent name
    name = getattr(args, "name", None) or ""
    if not name:
        from zynd_cli.tui import prompt as tui_prompt
        name = tui_prompt("Agent name")
    if not name:
        print("Error: Agent name is required.", file=sys.stderr)
        sys.exit(1)

    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Name: [bold]{name}[/bold]")

    # 4. Derive agent keypair into ~/.zynd/agents/<entity_name>/keypair.json
    ensure_zynd_dir()
    dev_kp = load_keypair(str(dev_key))

    index = getattr(args, "index", None)
    if index is None:
        d = agents_dir()
        used_indices = set()
        if d.exists():
            for agent_folder in d.iterdir():
                kp_file = agent_folder / "keypair.json"
                if kp_file.exists():
                    try:
                        _, meta = load_keypair_with_metadata(str(kp_file))
                        if meta is not None:
                            idx = meta.get("index")
                            if idx is not None:
                                used_indices.add(idx)
                    except Exception:
                        pass
        index = 0
        while index in used_indices:
            index += 1

    a_dir = agent_dir(name)
    a_dir.mkdir(parents=True, exist_ok=True)
    kp_path = a_dir / "keypair.json"

    if kp_path.exists():
        kp = load_keypair(str(kp_path))
        console.print(f"  [dim]Using existing keypair:[/dim] {kp_path}")
    else:
        kp = derive_agent_keypair(dev_kp.private_key, index)
        save_keypair(kp, str(kp_path), derivation_metadata={
            "developer_public_key": dev_kp.public_key_b64,
            "index": index,
        })
        console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Derived keypair at index {index}")

    console.print(f"  [dim]Agent ID:[/dim]   {kp.entity_id}")
    console.print(f"  [dim]Public key:[/dim] {kp.public_key_string}")

    # 5. Check for existing files
    if os.path.exists("agent.py"):
        console.print("\n  [bold yellow]Warning:[/bold yellow] agent.py already exists.")
        overwrite = input("  Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            console.print("  [dim]Aborted.[/dim]")
            return

    # 5b. Preview the ZNS slug so the user sees which FQAN they're
    # about to bind. Not stored — runtime re-derives via slugify_name.
    entity_name_zns = slugify_name(name, "-agent")
    console.print(f"  [bold #8B5CF6]\u2713[/bold #8B5CF6] Agent name (ZNS): [bold]{entity_name_zns}[/bold]")

    # 6. Minimal config (deploy-config lives in .env; derivable fields
    # excluded; entity_name auto-derived from `name` at runtime).
    registry_url = get_registry_url(getattr(args, "registry", None))
    config = {
        "name": name,
        "framework": framework,
        "description": f"{name} agent",
        "category": "general",
        "tags": [],
        "summary": "",
        "webhook_port": 5000,
        "entity_index": index,
        "entity_pricing": None,
    }

    with open("agent.config.json", "w") as f:
        json.dump(config, f, indent=2)

    # 7. Create .env
    env_lines = [
        f"ZYND_AGENT_KEYPAIR_PATH={kp_path}",
        f"ZYND_REGISTRY_URL={registry_url}",
        "",
    ]
    for key in fw_info.get("env_keys", []):
        env_lines.append(f"{key}=")
    _write_file(".env", "\n".join(env_lines) + "\n")

    # 8. Create agent.py from template
    tpl_name = framework.replace("-", "_") + ".py.tpl"
    tpl_path = Path(__file__).parent.parent / "templates" / tpl_name

    if tpl_path.exists():
        template = tpl_path.read_text()
        agent_code = template.replace("__AGENT_NAME__", name)
        _write_file("agent.py", agent_code)
    else:
        console.print(f"  [yellow]Warning: Template not found: {tpl_name}[/yellow]")

    # 9. Create .well-known/ directory
    os.makedirs(".well-known", exist_ok=True)
    _write_file(".well-known/agent.json", json.dumps(
        {"_note": "This file is auto-generated when the agent runs. Do not edit manually."},
        indent=2,
    ))

    console.print()
    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] agent.config.json")
    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] .env")
    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] agent.py")
    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] .well-known/agent.json")

    # 10. Summary
    console.print()
    console.print(f"  [bold green]Agent project created![/bold green]")
    console.print()
    console.print(f"  [dim]Name[/dim]       {name}")
    console.print(f"  [dim]Framework[/dim]   {fw_info['label']}")
    console.print(f"  [dim]Agent ID[/dim]    {kp.entity_id}")
    console.print(f"  [dim]Keypair[/dim]     {kp_path}")
    console.print()
    console.print(f"  [bold]Next steps:[/bold]")
    console.print(f"  [dim]1.[/dim] Install dependencies:")
    console.print(f"     [bold #8B5CF6]{fw_info['install']}[/bold #8B5CF6]")
    step = 2
    if fw_info.get("env_keys"):
        console.print(f"  [dim]{step}.[/dim] Add your API keys to [bold].env[/bold]")
        step += 1
    console.print(f"  [dim]{step}.[/dim] Run your agent (registers on first run, updates on subsequent runs):")
    console.print(f"     [bold #8B5CF6]zynd agent run[/bold #8B5CF6]")


def _agent_run(args: argparse.Namespace):
    """Delegate to the shared EntityRunner base class."""
    AgentRunner().run(args)


def _compute_codebase_hash(root_dir: str) -> str:
    """SHA-256 over source files under root_dir.

    Includes: .py, .json (except agent.config.json), .toml, .yaml, .yml, .txt, .md, .cfg
    Excludes: .env, __pycache__, .git, node_modules, .well-known, .agent*
    """
    root = Path(root_dir).resolve()

    skip_dirs = {"__pycache__", ".git", "node_modules", ".well-known", ".venv", "venv", ".agent"}
    skip_files = {".env", "agent.config.json"}
    source_exts = {".py", ".json", ".toml", ".yaml", ".yml", ".txt", ".md", ".cfg"}

    file_hashes = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".agent")]

        rel_dir = Path(dirpath).relative_to(root)
        for fname in sorted(filenames):
            if fname in skip_files or fname.startswith("."):
                continue
            if Path(fname).suffix.lower() not in source_exts:
                continue

            fpath = Path(dirpath) / fname
            rel_path = rel_dir / fname
            try:
                content = fpath.read_bytes()
                file_hash = hashlib.sha256(content).hexdigest()
                file_hashes.append(f"{rel_path}:{file_hash}")
            except (OSError, PermissionError):
                continue

    file_hashes.sort()
    combined = "\n".join(file_hashes).encode()
    return hashlib.sha256(combined).hexdigest()


def _write_file(path: str, content: str):
    """Write content to file, creating parent dirs if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

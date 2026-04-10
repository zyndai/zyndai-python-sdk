"""zynd agent — Agent project scaffolding, registration, and updates."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from zyndai_agent.ed25519_identity import (
    load_keypair,
    load_keypair_with_metadata,
    save_keypair,
    derive_agent_keypair,
    create_derivation_proof,
    generate_developer_id,
    sign as ed25519_sign,
)
from zyndai_agent.dns_registry import (
    register_agent,
    get_agent,
    update_agent,
    check_agent_name_available,
    get_agent_fqan,
)
from zynd_cli.config import (
    developer_key_path,
    agents_dir,
    agent_dir,
    agent_keypair_path,
    ensure_zynd_dir,
    get_registry_url,
)
from zynd_cli.templates import FRAMEWORKS, FRAMEWORK_ORDER


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser("agent", help="Agent project management", parents=parents or [])
    sub = p.add_subparsers(dest="agent_command")

    # zynd agent init
    init_p = sub.add_parser("init", help="Create a new agent project")
    init_p.add_argument("--name", help="Agent name (skips interactive prompt)")
    init_p.add_argument("--framework", choices=list(FRAMEWORKS.keys()), help="Framework (skips interactive prompt)")
    init_p.add_argument("--index", type=int, default=None, help="Derivation index (default: next available)")
    init_p.set_defaults(func=_agent_init)

    # zynd agent register
    reg_p = sub.add_parser("register", help="Register agent on the network")
    reg_p.add_argument("--config", default="agent.config.json", help="Path to agent.config.json")
    reg_p.add_argument("--agent-url", help="Override agent URL for registration")
    reg_p.set_defaults(func=_agent_register)

    # zynd agent update
    upd_p = sub.add_parser("update", help="Push config & codebase changes to registry")
    upd_p.add_argument("--config", default="agent.config.json", help="Path to agent.config.json")
    upd_p.set_defaults(func=_agent_update)

    # zynd agent run
    run_p = sub.add_parser("run", help="Run the agent from current directory")
    run_p.add_argument("--port", type=int, help="Override webhook port")
    run_p.set_defaults(func=_agent_run)

    p.set_defaults(func=_agent_help)


def _agent_help(args: argparse.Namespace):
    print("Usage: zynd agent {init,register,update,run}")
    print()
    print("Commands:")
    print("  init       Create a new agent project (interactive wizard)")
    print("  register   Register agent on the AgentDNS network")
    print("  update     Push config & codebase changes to registry")
    print("  run        Run the agent from current directory")


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

    # 4. Derive agent keypair into ~/.zynd/agents/<agent_name>/keypair.json
    ensure_zynd_dir()
    dev_kp = load_keypair(str(dev_key))

    # Determine derivation index
    index = getattr(args, "index", None)
    if index is None:
        # Find next available index by scanning existing agent dirs
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

    # Create agent directory
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

    console.print(f"  [dim]Agent ID:[/dim]   {kp.agent_id}")
    console.print(f"  [dim]Public key:[/dim] {kp.public_key_string}")

    # 5. Check for existing files
    if os.path.exists("agent.py"):
        console.print("\n  [bold yellow]Warning:[/bold yellow] agent.py already exists.")
        overwrite = input("  Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            console.print("  [dim]Aborted.[/dim]")
            return

    # 5b. Derive agent_name (ZNS identifier) from the agent name
    # Lowercase, replace spaces/underscores with hyphens, strip non-alphanumeric
    import re
    agent_name_zns = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-").replace("_", "-"))
    agent_name_zns = re.sub(r"-+", "-", agent_name_zns).strip("-")
    if len(agent_name_zns) < 3:
        agent_name_zns = agent_name_zns + "-agent"

    console.print(f"  [bold #8B5CF6]\u2713[/bold #8B5CF6] Agent name (ZNS): [bold]{agent_name_zns}[/bold]")

    # 6. Create agent.config.json
    registry_url = get_registry_url(getattr(args, "registry", None))
    config = {
        "name": name,
        "agent_name": agent_name_zns,
        "framework": framework,
        "description": f"{name} agent",
        "category": "general",
        "tags": [],
        "summary": "",
        "webhook_port": 5000,
        "registry_url": registry_url,
        "keypair_path": str(kp_path),
        "agent_index": index,
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
    console.print(f"  [dim]Agent ID[/dim]    {kp.agent_id}")
    console.print(f"  [dim]Keypair[/dim]     {kp_path}")
    console.print()
    console.print(f"  [bold]Next steps:[/bold]")
    console.print(f"  [dim]1.[/dim] Install dependencies:")
    console.print(f"     [bold #8B5CF6]{fw_info['install']}[/bold #8B5CF6]")
    step = 2
    if fw_info.get("env_keys"):
        console.print(f"  [dim]{step}.[/dim] Add your API keys to [bold].env[/bold]")
        step += 1
    console.print(f"  [dim]{step}.[/dim] Register your agent:")
    console.print(f"     [bold #8B5CF6]zynd agent register[/bold #8B5CF6]")
    step += 1
    console.print(f"  [dim]{step}.[/dim] Run your agent:")
    console.print(f"     [bold #8B5CF6]zynd agent run[/bold #8B5CF6]")


def _agent_register(args: argparse.Namespace):
    """Register agent on the network from agent.config.json."""

    config_path = args.config
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.", file=sys.stderr)
        print("Run 'zynd agent init' first to create an agent project.")
        sys.exit(1)

    with open(config_path, "r") as f:
        config = json.load(f)

    keypair_path = config.get("keypair_path")
    if not keypair_path or not os.path.exists(keypair_path):
        print(f"Error: Agent keypair not found at {keypair_path}", file=sys.stderr)
        sys.exit(1)

    kp = load_keypair(keypair_path)
    registry_url = get_registry_url(getattr(args, "registry", None)) or config.get("registry_url", "http://localhost:8080")

    # Check if already registered
    print(f"Checking registry at {registry_url}...")
    existing = get_agent(registry_url, kp.agent_id)
    already_registered = existing is not None

    if already_registered:
        print(f"Agent already registered: {kp.agent_id}")
        print(f"  Updating registration...")

    # Load developer key for proof
    dev_key = developer_key_path()
    if not dev_key.exists():
        print("Error: No developer keypair found.", file=sys.stderr)
        sys.exit(1)

    dev_kp = load_keypair(str(dev_key))

    # Get derivation metadata for proof
    _, metadata = load_keypair_with_metadata(keypair_path)
    derived_from = metadata.get("derived_from", {})
    agent_index = derived_from.get("index", config.get("agent_index", 0))

    proof = create_derivation_proof(dev_kp, kp.public_key, agent_index)

    # Build developer ID from developer public key
    dev_id = generate_developer_id(dev_kp.public_key_bytes)

    agent_url = args.agent_url or config.get("agent_url", f"http://localhost:{config.get('webhook_port', 5000)}")

    # Check agent name availability before registering (if agent_name is in config)
    agent_name_zns = config.get("agent_name")
    if agent_name_zns:
        # We need the developer's handle to check. Load it from the registry.
        # The developer handle is stored on the registry, so we check via the names API.
        print(f"Checking if agent name '{agent_name_zns}' is available...")
        # We need the developer handle — try to derive it from developer info on the registry
        # For now, use the dev_id to look up. The availability check uses the handle,
        # but we may not have it locally. Try to check via the registry API.
        try:
            # Try to get developer info to find the handle
            import requests as _req
            dev_resp = _req.get(f"{registry_url}/v1/developers/{dev_id}")
            if dev_resp.status_code == 404:
                print(f"  Warning: Developer {dev_id} not found on registry.", file=sys.stderr)
                print(f"  Ensure you completed onboarding: zynd auth login --registry {registry_url}", file=sys.stderr)
                agent_name_zns = None
            elif dev_resp.status_code == 200:
                dev_info = dev_resp.json()
                dev_handle = dev_info.get("dev_handle", "")
                if dev_handle:
                    avail = check_agent_name_available(registry_url, dev_handle, agent_name_zns)
                    if not avail.get("available", True):
                        existing_id = avail.get("existing_agent_id", "")
                        # Only fail if the existing agent has a different key
                        if existing_id and existing_id != kp.agent_id:
                            print(f"\nError: Agent name '{agent_name_zns}' is already taken under '{dev_handle}'.", file=sys.stderr)
                            print(f"  Existing agent: {existing_id}", file=sys.stderr)
                            print(f"  Choose a different agent_name in agent.config.json.", file=sys.stderr)
                            sys.exit(1)
                        elif not existing_id:
                            reason = avail.get("reason", "already taken")
                            print(f"\nError: Agent name '{agent_name_zns}' is not available: {reason}", file=sys.stderr)
                            print(f"  Update agent_name in agent.config.json.", file=sys.stderr)
                            sys.exit(1)
                    else:
                        print(f"  Agent name '{agent_name_zns}' is available.")
                else:
                    print(f"  Warning: Developer has no handle claimed on this registry.", file=sys.stderr)
                    print(f"  Agent name binding will be skipped.", file=sys.stderr)
                    print(f"  Ensure you completed onboarding at the dashboard, or re-run:", file=sys.stderr)
                    print(f"    zynd auth login --registry {registry_url}", file=sys.stderr)
                    agent_name_zns = None
            else:
                print(f"  Warning: Could not fetch developer info (HTTP {dev_resp.status_code}).", file=sys.stderr)
                agent_name_zns = None
        except Exception as e:
            print(f"  Warning: Could not check agent name availability: {e}")

    if already_registered:
        # Update the existing registration with correct data
        print(f"Updating agent on the network...")
        update_body = {
            "name": config["name"],
            "agent_url": agent_url,
            "category": config.get("category", "general"),
            "tags": config.get("tags", []),
            "summary": config.get("summary", ""),
        }
        success = update_agent(registry_url, kp.agent_id, kp, update_body)
        if success:
            fqan = get_agent_fqan(registry_url, kp.agent_id)
            print(f"\nAgent updated!")
            print(f"  Agent ID: {kp.agent_id}")
            if fqan:
                print(f"  FQAN:     {fqan}")
            print(f"  Name:     {config['name']}")
            print(f"  URL:      {agent_url}")
        else:
            print(f"Update failed.", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Registering agent on the network...")
        try:
            agent_id = register_agent(
                registry_url=registry_url,
                keypair=kp,
                name=config["name"],
                agent_url=agent_url,
                category=config.get("category", "general"),
                tags=config.get("tags", []),
                summary=config.get("summary", ""),
                developer_id=dev_id,
                developer_proof=proof,
                agent_name=agent_name_zns,
            )
            fqan = get_agent_fqan(registry_url, agent_id)
            print(f"\nAgent registered!")
            print(f"  Agent ID: {agent_id}")
            if fqan:
                print(f"  FQAN:     {fqan}")
            print(f"  Name:     {config['name']}")
            if agent_name_zns:
                print(f"  ZNS Name: {agent_name_zns}")
            print(f"  URL:      {agent_url}")
        except Exception as e:
            print(f"Registration failed: {e}", file=sys.stderr)
            sys.exit(1)


def _agent_update(args: argparse.Namespace):
    """Push config and codebase changes to the registry."""

    config_path = args.config
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.", file=sys.stderr)
        print("Run 'zynd agent init' first.")
        sys.exit(1)

    with open(config_path, "r") as f:
        config = json.load(f)

    keypair_path = config.get("keypair_path")
    if not keypair_path or not os.path.exists(keypair_path):
        print(f"Error: Agent keypair not found at {keypair_path}", file=sys.stderr)
        sys.exit(1)

    kp = load_keypair(keypair_path)
    registry_url = get_registry_url(getattr(args, "registry", None)) or config.get("registry_url", "http://localhost:8080")

    from rich.console import Console
    console = Console()

    # Check if agent is registered
    console.print(f"  [dim]Checking registry...[/dim]")
    existing = get_agent(registry_url, kp.agent_id)
    if not existing:
        console.print(f"  [bold red]✗[/bold red] Agent not registered. Run 'zynd agent register' first.")
        sys.exit(1)

    # Compute codebase hash (SHA-256 of all source files in current directory)
    console.print(f"  [dim]Computing codebase hash...[/dim]")
    codebase_hash = _compute_codebase_hash(".")
    console.print(f"  [dim]Hash:[/dim] {codebase_hash[:16]}...")

    # Build update payload from config
    agent_url = config.get("agent_url", f"http://localhost:{config.get('webhook_port', 5000)}")

    # Build update body — all mutable fields from agent.config.json
    update_body = {
        "name": config.get("name", ""),
        "agent_url": agent_url,
        "category": config.get("category", "general"),
        "tags": config.get("tags", []),
        "summary": config.get("summary", ""),
        "codebase_hash": codebase_hash,
    }

    # Push to registry (update_agent handles signing + auth)
    console.print(f"  [dim]Pushing update to registry...[/dim]")
    success = update_agent(registry_url, kp.agent_id, kp, update_body)

    if success:
        console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Agent updated on registry")
        console.print(f"  [dim]Agent ID:[/dim]      {kp.agent_id}")
        console.print(f"  [dim]Name:[/dim]          {config.get('name', '')}")
        console.print(f"  [dim]Agent URL:[/dim]     {agent_url}")
        console.print(f"  [dim]Category:[/dim]      {config.get('category', 'general')}")
        console.print(f"  [dim]Tags:[/dim]          {', '.join(config.get('tags', []))}")
        console.print(f"  [dim]Codebase hash:[/dim] {codebase_hash[:16]}...")
    else:
        console.print(f"  [bold red]✗[/bold red] Update failed")
        sys.exit(1)


def _compute_codebase_hash(root_dir: str) -> str:
    """Compute SHA-256 hash of all source files in the agent's directory.

    Includes: .py, .json (except agent.config.json), .toml, .yaml, .yml, .txt
    Excludes: .env, __pycache__, .git, node_modules, .well-known, .agent*
    """
    hasher = hashlib.sha256()
    root = Path(root_dir).resolve()

    skip_dirs = {"__pycache__", ".git", "node_modules", ".well-known", ".venv", "venv", ".agent"}
    skip_files = {".env", "agent.config.json"}
    source_exts = {".py", ".json", ".toml", ".yaml", ".yml", ".txt", ".md", ".cfg"}

    file_hashes = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs and excluded dirs
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".agent")]

        rel_dir = Path(dirpath).relative_to(root)
        for fname in sorted(filenames):
            if fname in skip_files:
                continue
            if fname.startswith("."):
                continue
            ext = Path(fname).suffix.lower()
            if ext not in source_exts:
                continue

            fpath = Path(dirpath) / fname
            rel_path = rel_dir / fname
            try:
                content = fpath.read_bytes()
                file_hash = hashlib.sha256(content).hexdigest()
                file_hashes.append(f"{rel_path}:{file_hash}")
            except (OSError, PermissionError):
                continue

    # Sort for deterministic order, then hash the combined list
    file_hashes.sort()
    combined = "\n".join(file_hashes).encode()
    return hashlib.sha256(combined).hexdigest()




def _agent_run(args: argparse.Namespace):
    """Run the agent from the current directory using agent.config.json."""
    import subprocess

    config_file = "agent.config.json"
    if not os.path.exists(config_file):
        print("Error: agent.config.json not found in current directory.", file=sys.stderr)
        print("Make sure you're in the agent's root directory, or run 'zynd agent init' first.")
        sys.exit(1)

    if not os.path.exists("agent.py"):
        print("Error: agent.py not found in current directory.", file=sys.stderr)
        sys.exit(1)

    with open(config_file) as f:
        config = json.load(f)

    # Override port if specified
    port = getattr(args, "port", None)
    if port:
        os.environ["ZYND_WEBHOOK_PORT"] = str(port)

    # Ensure keypair path is set in env
    keypair_path = config.get("keypair_path", "")
    if keypair_path and not os.environ.get("ZYND_AGENT_KEYPAIR_PATH"):
        os.environ["ZYND_AGENT_KEYPAIR_PATH"] = keypair_path

    # Ensure registry URL is set
    registry_url = config.get("registry_url", "")
    if registry_url and not os.environ.get("ZYND_REGISTRY_URL"):
        os.environ["ZYND_REGISTRY_URL"] = registry_url

    from rich.console import Console
    console = Console()
    console.print()
    console.print(f"  [bold #8B5CF6]▶[/bold #8B5CF6] Running [bold]{config.get('name', 'agent')}[/bold] ({config.get('framework', 'custom')})")
    console.print(f"  [dim]Keypair:[/dim] {keypair_path}")
    console.print(f"  [dim]Registry:[/dim] {registry_url}")
    console.print()

    try:
        subprocess.run([sys.executable, "agent.py"], check=True)
    except KeyboardInterrupt:
        console.print("\n  [dim]Agent stopped.[/dim]")
    except subprocess.CalledProcessError as e:
        print(f"Agent exited with code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


def _write_file(path: str, content: str):
    """Write content to file, creating parent dirs if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

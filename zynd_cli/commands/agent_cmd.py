"""zynd agent — Agent project scaffolding and unified run."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


def _slugify_name(name: str) -> str:
    """Convert a free-form display name to a ZNS-safe slug.

    Same rules as service_cmd._slugify_name — lowercase, spaces/
    underscores → hyphens, drop non-alphanumeric, collapse repeated
    hyphens, trim ends. Kept as a local helper (instead of a shared
    module) to avoid a circular-import hazard between the two command
    modules.
    """
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-").replace("_", "-"))
    slug = re.sub(r"-+", "-", slug).strip("-")
    if len(slug) < 3:
        slug = slug + "-agent"
    if len(slug) > 36:
        slug = slug[:36]
    return slug

from zyndai_agent.ed25519_identity import (
    load_keypair,
    load_keypair_with_metadata,
    save_keypair,
    derive_agent_keypair,
    create_derivation_proof,
    generate_developer_id,
)
from zyndai_agent.dns_registry import (
    register_entity,
    get_entity,
    update_entity,
    check_entity_name_available,
    get_entity_fqan,
)
from zynd_cli.config import (
    developer_key_path,
    agents_dir,
    agent_dir,
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

    console.print(f"  [dim]Agent ID:[/dim]   {kp.entity_id}")
    console.print(f"  [dim]Public key:[/dim] {kp.public_key_string}")

    # 5. Check for existing files
    if os.path.exists("agent.py"):
        console.print("\n  [bold yellow]Warning:[/bold yellow] agent.py already exists.")
        overwrite = input("  Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            console.print("  [dim]Aborted.[/dim]")
            return

    # 5b. Derive the ZNS identifier (slug form) from the agent name. We
    # compute it here only to PREVIEW it to the user during init so they
    # can see what FQAN their agent will bind to — it is NOT stored in
    # the config file. At runtime, _agent_run re-derives the slug from
    # config["name"] via the same helper, and operators can override it
    # by adding an explicit "entity_name" key to the config (rare path
    # for when the display name should differ from the ZNS handle).
    entity_name_zns = _slugify_name(name)

    console.print(f"  [bold #8B5CF6]\u2713[/bold #8B5CF6] Agent name (ZNS): [bold]{entity_name_zns}[/bold]")

    # 6. Create agent.config.json with a minimal canonical schema.
    #
    # Deploy-config (keypair_path, registry_url) lives in .env only —
    # 12-factor split. Derivable fields (entity_url, entity_type,
    # webhook_host, price, entity_name) also stay out:
    #
    #   - entity_name is a slugified version of `name`; runtime computes
    #     it via _slugify_name(config["name"]) and the user can still
    #     override by adding an explicit "entity_name" field to the JSON
    #     if they want a custom ZNS handle that isn't the auto-slug.
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
    """Start the agent, health-check it, upsert on the registry, and keep running."""
    import subprocess
    import time
    import requests as _req

    # Pull .env into os.environ so ZYND_AGENT_KEYPAIR_PATH and
    # ZYND_REGISTRY_URL are available to both this CLI process and the
    # child agent.py subprocess. load_dotenv() by default doesn't override
    # already-set env vars, so anything the user exported in their shell
    # still wins.
    # load_dotenv() with no args walks up from the *calling file* (the
    # installed package path), not the user's cwd — so it never finds the
    # project's .env. Point it explicitly at ./.env in the current dir.
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path.cwd() / ".env")
    except ImportError:
        pass

    config_path = args.config
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.", file=sys.stderr)
        print("Run 'zynd agent init' first to create an agent project.")
        sys.exit(1)

    if not os.path.exists("agent.py"):
        print("Error: agent.py not found in current directory.", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r") as f:
        config = json.load(f)

    # Keypair path comes from ZYND_AGENT_KEYPAIR_PATH in .env (canonical
    # location as of the config-minimization pass). Fall back to the
    # legacy config.json location for backward compat with projects
    # scaffolded before the split.
    keypair_path = os.environ.get("ZYND_AGENT_KEYPAIR_PATH") or config.get("keypair_path")
    if not keypair_path:
        print(
            "Error: agent keypair path not set. Export ZYND_AGENT_KEYPAIR_PATH "
            "or add it to .env in the current directory.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not os.path.exists(keypair_path):
        print(f"Error: agent keypair not found at {keypair_path}", file=sys.stderr)
        sys.exit(1)

    kp = load_keypair(keypair_path)
    # get_registry_url already walks CLI flag → ZYND_REGISTRY_URL → ~/.zynd/config.json → default
    registry_url = get_registry_url(getattr(args, "registry", None))
    port = args.port or config.get("webhook_port", 5000)
    entity_url = args.entity_url or f"http://localhost:{port}"
    health_url = f"http://localhost:{port}/health"

    dev_key = developer_key_path()
    if not dev_key.exists():
        print("Error: No developer keypair found.", file=sys.stderr)
        sys.exit(1)

    dev_kp = load_keypair(str(dev_key))
    _, metadata = load_keypair_with_metadata(keypair_path)
    derived_from = (metadata or {}).get("derived_from", {})
    entity_index = derived_from.get("index", config.get("entity_index", 0))
    proof = create_derivation_proof(dev_kp, kp.public_key, entity_index)
    dev_id = generate_developer_id(dev_kp.public_key_bytes)

    from rich.console import Console
    console = Console()

    # --- Step 1: Start the agent as a background process ---
    console.print()
    console.print(f"  [bold #8B5CF6]▶[/bold #8B5CF6] Starting [bold]{config.get('name', 'agent')}[/bold]...")

    env = os.environ.copy()
    env["ZYND_AGENT_KEYPAIR_PATH"] = keypair_path
    env["ZYND_REGISTRY_URL"] = registry_url
    if args.port:
        env["ZYND_WEBHOOK_PORT"] = str(args.port)

    proc = subprocess.Popen(
        [sys.executable, "agent.py"],
        env=env,
        stdout=None,
        stderr=None,
    )

    # --- Step 2: Health-check (poll /health for up to 15s) ---
    console.print(f"  [dim]Waiting for agent to start (health: {health_url})...[/dim]")
    healthy = False
    for _ in range(30):
        if proc.poll() is not None:
            console.print(f"  [bold red]✗[/bold red] Agent process exited with code {proc.returncode}")
            sys.exit(1)
        try:
            resp = _req.get(health_url, timeout=1)
            if resp.status_code == 200:
                healthy = True
                break
        except _req.ConnectionError:
            pass
        time.sleep(0.5)

    if not healthy:
        console.print(f"  [bold red]✗[/bold red] Agent did not become healthy within 15s")
        proc.terminate()
        sys.exit(1)

    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Agent is healthy")

    # --- Step 3: Check name availability ---
    # entity_name is either an explicit override in config.json (rare) or
    # slugified on the fly from the display name (the common case).
    entity_name_zns = config.get("entity_name") or _slugify_name(config.get("name", ""))
    if entity_name_zns:
        try:
            dev_resp = _req.get(f"{registry_url}/v1/developers/{dev_id}")
            if dev_resp.status_code == 200:
                dev_handle = dev_resp.json().get("dev_handle", "")
                if dev_handle:
                    avail = check_entity_name_available(registry_url, dev_handle, entity_name_zns)
                    if not avail.get("available", True):
                        existing_id = avail.get("existing_entity_id", "")
                        if existing_id and existing_id != kp.entity_id:
                            console.print(f"  [bold red]✗[/bold red] Name '{entity_name_zns}' already taken by {existing_id}")
                            proc.terminate()
                            sys.exit(1)
                    else:
                        console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Name '{entity_name_zns}' is available")
                else:
                    console.print(f"  [dim]Warning: No developer handle — name binding skipped[/dim]")
                    entity_name_zns = None
            elif dev_resp.status_code == 404:
                console.print(f"  [dim]Warning: Developer not found on registry — name binding skipped[/dim]")
                entity_name_zns = None
        except Exception as e:
            console.print(f"  [dim]Warning: Could not check name: {e}[/dim]")

    # --- Step 4: Upsert on registry ---
    existing = get_entity(registry_url, kp.entity_id)

    if existing is not None:
        console.print(f"  [dim]Agent already registered — updating...[/dim]")
        codebase_hash = _compute_codebase_hash(".")
        update_body = {
            "name": config["name"],
            "entity_url": entity_url,
            "category": config.get("category", "general"),
            "tags": config.get("tags", []),
            "summary": config.get("summary", ""),
            "codebase_hash": codebase_hash,
        }
        success = update_entity(registry_url, kp.entity_id, kp, update_body)
        if success:
            fqan = get_entity_fqan(registry_url, kp.entity_id)
            console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Agent updated on registry")
            console.print(f"  [dim]Codebase hash:[/dim] {codebase_hash[:16]}...")
            if fqan:
                console.print(f"  [dim]FQAN:[/dim]     [bold #F59E0B]{fqan}[/bold #F59E0B]")
        else:
            console.print(f"  [bold red]✗[/bold red] Update failed")
    else:
        try:
            entity_id = register_entity(
                registry_url=registry_url,
                keypair=kp,
                name=config["name"],
                entity_url=entity_url,
                category=config.get("category", "general"),
                tags=config.get("tags", []),
                summary=config.get("summary", ""),
                developer_id=dev_id,
                developer_proof=proof,
                entity_name=entity_name_zns,
                entity_pricing=config.get("entity_pricing"),
            )
            fqan = get_entity_fqan(registry_url, entity_id)
            console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Agent registered: {entity_id}")
            if fqan:
                console.print(f"  [dim]FQAN:[/dim]     [bold #F59E0B]{fqan}[/bold #F59E0B]")
            if entity_name_zns:
                console.print(f"  [dim]ZNS Name:[/dim] {entity_name_zns}")
        except Exception as e:
            console.print(f"  [bold red]✗[/bold red] Registration failed: {e}")

    # --- Step 5: Keep the agent running ---
    console.print()
    console.print(f"  [bold green]Agent is running.[/bold green] Press Ctrl+C to stop.")
    console.print()
    try:
        proc.wait()
    except KeyboardInterrupt:
        console.print(f"\n  [dim]Stopping agent...[/dim]")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _compute_codebase_hash(root_dir: str) -> str:
    """Compute SHA-256 hash of all source files in the agent's directory.

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

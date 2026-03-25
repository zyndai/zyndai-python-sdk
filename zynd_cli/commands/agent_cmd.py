"""zynd agent — Agent project scaffolding and registration."""

import argparse
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
    generate_agent_id,
)
from zyndai_agent.dns_registry import register_agent, get_agent
from zynd_cli.config import (
    developer_key_path,
    agents_dir,
    agent_dir,
    agent_keypair_path,
    ensure_zynd_dir,
    get_registry_url,
)
from zynd_cli.templates import FRAMEWORKS, FRAMEWORK_ORDER


def register_parser(subparsers: argparse._SubParsersAction):
    p = subparsers.add_parser("agent", help="Agent project management")
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

    # zynd agent run
    run_p = sub.add_parser("run", help="Run the agent from current directory")
    run_p.add_argument("--port", type=int, help="Override webhook port")
    run_p.set_defaults(func=_agent_run)

    p.set_defaults(func=_agent_help)


def _agent_help(args: argparse.Namespace):
    print("Usage: zynd agent {init,register,run}")
    print()
    print("Commands:")
    print("  init       Create a new agent project (interactive wizard)")
    print("  register   Register agent on the AgentDNS network")
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
                        idx = meta.get("derived_from", {}).get("index")
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

    # 6. Create agent.config.json
    registry_url = get_registry_url(getattr(args, "registry", None))
    config = {
        "name": name,
        "framework": framework,
        "description": f"{name} agent",
        "category": "general",
        "tags": [],
        "summary": "",
        "webhook_port": 5000,
        "registry_url": registry_url,
        "keypair_path": str(kp_path),
        "agent_index": index,
        "auto_register": True,
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
        agent_code = template.replace("{agent_name}", name)
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
    console.print(f"  [dim]{step}.[/dim] Run your agent:")
    console.print(f"     [bold #8B5CF6]python agent.py[/bold #8B5CF6]")
    console.print()
    console.print(f"  [dim]The agent will auto-register on the network when it starts.[/dim]")


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
    if existing:
        print(f"Agent already registered: {kp.agent_id}")
        print(f"  Name: {existing.get('name', '?')}")
        return

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
    dev_id = "agdns:dev:" + generate_agent_id(dev_kp.public_key_bytes).replace("agdns:", "")

    agent_url = args.agent_url or config.get("agent_url", f"http://localhost:{config.get('webhook_port', 5000)}")

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
        )
        print(f"\nAgent registered!")
        print(f"  Agent ID: {agent_id}")
        print(f"  Name:     {config['name']}")
        print(f"  URL:      {agent_url}")
    except Exception as e:
        print(f"Registration failed: {e}", file=sys.stderr)
        sys.exit(1)


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

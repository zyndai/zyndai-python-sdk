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

    p.set_defaults(func=_agent_help)


def _agent_help(args: argparse.Namespace):
    print("Usage: zynd agent {init,register}")
    print()
    print("Commands:")
    print("  init       Create a new agent project (interactive wizard)")
    print("  register   Register agent on the AgentDNS network")


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
        print("\nSelect a framework:\n")
        for i, fw_key in enumerate(FRAMEWORK_ORDER, 1):
            fw = FRAMEWORKS[fw_key]
            print(f"  {i}. {fw['label']:15s} — {fw['description']}")
        print()

        while True:
            try:
                choice = input("Enter number (1-5): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(FRAMEWORK_ORDER):
                    framework = FRAMEWORK_ORDER[idx]
                    break
            except (ValueError, EOFError):
                pass
            print("Invalid choice. Enter a number 1-5.")

    fw_info = FRAMEWORKS[framework]
    print(f"\nFramework: {fw_info['label']}")

    # 3. Get agent name
    name = getattr(args, "name", None) or ""
    if not name:
        name = input("Agent name: ").strip()
    if not name:
        print("Error: Agent name is required.", file=sys.stderr)
        sys.exit(1)

    print(f"Agent name: {name}")

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
        print(f"\nUsing existing keypair: {kp_path}")
    else:
        kp = derive_agent_keypair(dev_kp.private_key, index)
        save_keypair(kp, str(kp_path), derivation_metadata={
            "developer_public_key": dev_kp.public_key_b64,
            "index": index,
        })
        print(f"\nDerived keypair at index {index}: {kp_path}")

    print(f"  Agent ID:   {kp.agent_id}")
    print(f"  Public key: {kp.public_key_string}")

    # 5. Check for existing files
    if os.path.exists("agent.py"):
        print("\nWarning: agent.py already exists in current directory.", file=sys.stderr)
        overwrite = input("Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            print("Aborted.")
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
    print(f"\nCreated agent.config.json")

    # 7. Create .env
    env_lines = [
        f"ZYND_AGENT_KEYPAIR_PATH={kp_path}",
        f"ZYND_REGISTRY_URL={registry_url}",
        "",
    ]
    for key in fw_info.get("env_keys", []):
        env_lines.append(f"{key}=")

    _write_file(".env", "\n".join(env_lines) + "\n")
    print("Created .env")

    # 8. Create agent.py from template
    tpl_name = framework.replace("-", "_") + ".py.tpl"
    tpl_path = Path(__file__).parent.parent / "templates" / tpl_name

    if tpl_path.exists():
        template = tpl_path.read_text()
        # Replace {agent_name} placeholder, but leave {query}, {str(e)}, etc. alone
        agent_code = template.replace("{agent_name}", name)
        _write_file("agent.py", agent_code)
        print("Created agent.py")
    else:
        print(f"Warning: Template not found: {tpl_name}", file=sys.stderr)

    # 9. Create .well-known/ directory
    os.makedirs(".well-known", exist_ok=True)
    _write_file(".well-known/agent.json", json.dumps(
        {"_note": "This file is auto-generated when the agent runs. Do not edit manually."},
        indent=2,
    ))
    print("Created .well-known/agent.json (placeholder)")

    # 10. Summary
    print(f"\n{'=' * 50}")
    print(f"Agent project created!")
    print(f"{'=' * 50}")
    print(f"  Name:      {name}")
    print(f"  Framework: {fw_info['label']}")
    print(f"  Agent ID:  {kp.agent_id}")
    print(f"  Keypair:   {kp_path}")
    print()
    print(f"Next steps:")
    print(f"  1. Install dependencies:")
    print(f"     {fw_info['install']}")
    if fw_info.get("env_keys"):
        print(f"  2. Add your API keys to .env")
        print(f"  3. Run your agent:")
    else:
        print(f"  2. Run your agent:")
    print(f"     python agent.py")
    print()
    print(f"The agent will auto-register on the network when it starts.")


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


def _write_file(path: str, content: str):
    """Write content to file, creating parent dirs if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

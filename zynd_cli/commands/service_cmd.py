"""zynd service — Service project scaffolding, registration, and updates."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from zyndai_agent.ed25519_identity import (
    load_keypair,
    load_keypair_with_metadata,
    save_keypair,
    derive_agent_keypair,
    create_derivation_proof,
    generate_developer_id,
    generate_service_id,
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
    services_dir,
    service_dir,
    service_keypair_path,
    ensure_zynd_dir,
    get_registry_url,
)


def register_parser(subparsers: argparse._SubParsersAction, parents=None):
    p = subparsers.add_parser(
        "service", help="Service project management", parents=parents or []
    )
    sub = p.add_subparsers(dest="service_command")

    # zynd service init
    init_p = sub.add_parser("init", help="Create a new service project")
    init_p.add_argument("--name", help="Service name (skips interactive prompt)")
    init_p.add_argument("--index", type=int, default=None, help="Derivation index")
    init_p.set_defaults(func=_service_init)

    # zynd service register
    reg_p = sub.add_parser("register", help="Register service on the network")
    reg_p.add_argument(
        "--config", default="service.config.json", help="Path to service.config.json"
    )
    reg_p.set_defaults(func=_service_register)

    # zynd service update
    upd_p = sub.add_parser("update", help="Push config changes to registry")
    upd_p.add_argument(
        "--config", default="service.config.json", help="Path to service.config.json"
    )
    upd_p.set_defaults(func=_service_update)

    # zynd service run
    run_p = sub.add_parser("run", help="Run the service from current directory")
    run_p.add_argument("--port", type=int, help="Override webhook port")
    run_p.set_defaults(func=_service_run)

    p.set_defaults(func=_service_help)


def _service_help(args):
    print("Usage: zynd service {init,register,update,run}")
    print("  init      Create a new service project")
    print("  register  Register service on the network")
    print("  update    Push config changes to registry")
    print("  run       Run the service")


def _to_zns_name(name: str) -> str:
    """Convert a display name to a ZNS-safe name with svc: prefix."""
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if len(slug) < 3:
        slug = slug + "-service"
    if len(slug) > 36:
        slug = slug[:36]
    return f"svc:{slug}"


def _service_init(args: argparse.Namespace):
    """Scaffold a new service project."""
    ensure_zynd_dir()
    dev_path = developer_key_path()

    if not dev_path.exists():
        print("Error: Developer keypair not found.", file=sys.stderr)
        print("  Run 'zynd auth login --registry <url>' first.", file=sys.stderr)
        sys.exit(1)

    dev_kp = load_keypair(str(dev_path))

    # Get service name
    name = args.name
    if not name:
        try:
            name = input("  Service name: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            sys.exit(0)

    if not name:
        print("Error: Service name is required.", file=sys.stderr)
        sys.exit(1)

    service_name_zns = _to_zns_name(name)

    # Determine derivation index
    if args.index is not None:
        index = args.index
    else:
        svc_dir = services_dir()
        existing = list(svc_dir.iterdir()) if svc_dir.exists() else []
        index = len(existing)

    # Derive keypair
    svc_kp = derive_agent_keypair(dev_kp.private_key, index)
    svc_id = generate_service_id(svc_kp.public_key_bytes)

    # Store keypair
    svc_safe = name.lower().replace(" ", "-")
    kp_dir = service_dir(svc_safe)
    kp_dir.mkdir(parents=True, exist_ok=True)
    kp_path = service_keypair_path(svc_safe)
    save_keypair(
        svc_kp,
        str(kp_path),
        derivation_metadata={
            "developer_public_key": dev_kp.public_key_b64,
            "index": index,
        },
    )

    registry_url = get_registry_url(getattr(args, "registry", None))

    # Get optional fields
    try:
        description = input("  Description (optional): ").strip()
        category = input("  Category [general]: ").strip() or "general"
        service_endpoint = input("  Service endpoint URL (optional): ").strip() or None
        openapi_url = input("  OpenAPI spec URL (optional): ").strip() or None
    except (KeyboardInterrupt, EOFError):
        description = ""
        category = "general"
        service_endpoint = None
        openapi_url = None

    # Write service.config.json
    config = {
        "name": name,
        "service_name": service_name_zns,
        "description": description,
        "category": category,
        "tags": [],
        "summary": "",
        "service_endpoint": service_endpoint,
        "openapi_url": openapi_url,
        "webhook_port": 5000,
        "registry_url": registry_url,
        "keypair_path": str(kp_path),
        "agent_index": index,
    }
    with open("service.config.json", "w") as f:
        json.dump(config, f, indent=2)

    # Write .env
    env_lines = [
        f'ZYND_SERVICE_KEYPAIR_PATH="{kp_path}"',
        f'ZYND_REGISTRY_URL="{registry_url}"',
    ]
    with open(".env", "w") as f:
        f.write("\n".join(env_lines) + "\n")

    # Write service.py from template
    tpl_dir = Path(__file__).parent.parent / "templates"
    tpl_path = tpl_dir / "service.py.tpl"
    if tpl_path.exists():
        content = tpl_path.read_text().replace("__SERVICE_NAME__", name)
        with open("service.py", "w") as f:
            f.write(content)

    # Create .well-known directory with placeholder (auto-generated at runtime)
    well_known = Path(".well-known")
    well_known.mkdir(exist_ok=True)
    with open(well_known / "agent.json", "w") as f:
        json.dump(
            {
                "name": name,
                "type": "service",
                "description": description,
                "category": category,
                "service_endpoint": service_endpoint,
                "openapi_url": openapi_url,
                "_note": "This file is auto-regenerated when the service runs.",
            },
            f,
            indent=2,
        )

    print(f"\n  Service project created!")
    print(f"    Name:       {name}")
    print(f"    ZNS Name:   {service_name_zns}")
    print(f"    Service ID: {svc_id}")
    print(f"    Keypair:    {kp_path}")
    print(f"\n  Next steps:")
    print(f"    1. Edit service.py with your service logic")
    print(f"    2. zynd service register")
    print(f"    3. zynd service run")


def _service_register(args: argparse.Namespace):
    """Register a service on the registry."""
    config_path = args.config
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    registry_url = get_registry_url(getattr(args, "registry", None))
    kp_path = config.get("keypair_path")
    if not kp_path or not os.path.exists(kp_path):
        print("Error: keypair_path not found in config.", file=sys.stderr)
        sys.exit(1)

    kp, meta = load_keypair_with_metadata(kp_path)
    dev_path = developer_key_path()
    if not dev_path.exists():
        print("Error: Developer keypair not found.", file=sys.stderr)
        sys.exit(1)

    dev_kp = load_keypair(str(dev_path))
    dev_id = generate_developer_id(dev_kp.public_key_bytes)

    # Create derivation proof
    derived = meta.get("derived_from", {})
    agent_index = derived.get("index", config.get("agent_index", 0))
    proof = create_derivation_proof(dev_kp, kp.public_key, agent_index)

    service_name_zns = config.get("service_name", "")

    # Auto-derive service_endpoint from webhook_port if not set
    service_endpoint = config.get("service_endpoint")
    if not service_endpoint:
        port = config.get("webhook_port", 5000)
        service_endpoint = f"http://localhost:{port}"

    # Check if already registered
    existing = get_agent(registry_url, kp.agent_id, entity_type="service")
    already_registered = existing is not None

    if already_registered:
        print(f"Updating service on the network...")
        update_body = {
            "name": config["name"],
            "category": config.get("category", "general"),
            "tags": config.get("tags", []),
            "summary": config.get("summary", ""),
        }
        success = update_agent(
            registry_url, kp.agent_id, kp, update_body, entity_type="service"
        )
        if success:
            fqan = get_agent_fqan(registry_url, kp.agent_id)
            print(f"\n  Service updated!")
            print(f"    Service ID: {kp.agent_id}")
            if fqan:
                print(f"    FQAN:       {fqan}")
        else:
            print("Update failed.", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Registering service on the network...")
        try:
            service_id = register_agent(
                registry_url=registry_url,
                keypair=kp,
                name=config["name"],
                agent_url=service_endpoint,
                category=config.get("category", "general"),
                tags=config.get("tags", []),
                summary=config.get("summary", ""),
                developer_id=dev_id,
                developer_proof=proof,
                agent_name=service_name_zns,
                entity_type="service",
                service_endpoint=service_endpoint,
                openapi_url=config.get("openapi_url"),
                entity_pricing=config.get("entity_pricing"),
            )
            fqan = get_agent_fqan(registry_url, service_id)
            print(f"\n  Service registered!")
            print(f"    Service ID: {service_id}")
            if fqan:
                print(f"    FQAN:       {fqan}")
            print(f"    Name:       {config['name']}")
            if service_name_zns:
                print(f"    ZNS Name:   {service_name_zns}")
        except Exception as e:
            print(f"Registration failed: {e}", file=sys.stderr)
            sys.exit(1)


def _service_update(args: argparse.Namespace):
    """Push config changes to the registry."""
    config_path = args.config
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    registry_url = get_registry_url(getattr(args, "registry", None))
    kp_path = config.get("keypair_path")
    if not kp_path or not os.path.exists(kp_path):
        print("Error: keypair_path not found.", file=sys.stderr)
        sys.exit(1)

    kp = load_keypair(kp_path)

    update_body = {
        "name": config["name"],
        "category": config.get("category", "general"),
        "tags": config.get("tags", []),
        "summary": config.get("summary", ""),
    }

    print(f"Updating service on the network...")
    success = update_agent(
        registry_url, kp.agent_id, kp, update_body, entity_type="service"
    )
    if success:
        fqan = get_agent_fqan(registry_url, kp.agent_id)
        print(f"\n  Service updated!")
        print(f"    Service ID: {kp.agent_id}")
        if fqan:
            print(f"    FQAN:       {fqan}")
    else:
        print("Update failed.", file=sys.stderr)
        sys.exit(1)


def _service_run(args: argparse.Namespace):
    """Run the service from current directory."""
    import subprocess

    if not os.path.exists("service.py"):
        print("Error: service.py not found in current directory.", file=sys.stderr)
        sys.exit(1)

    config = {}
    if os.path.exists("service.config.json"):
        with open("service.config.json") as f:
            config = json.load(f)

    env = os.environ.copy()
    kp_path = config.get("keypair_path")
    if kp_path:
        env["ZYND_SERVICE_KEYPAIR_PATH"] = kp_path
    registry_url = config.get("registry_url")
    if registry_url:
        env["ZYND_REGISTRY_URL"] = registry_url

    if args.port:
        env["ZYND_WEBHOOK_PORT"] = str(args.port)

    print(f"Starting service...")
    try:
        subprocess.run([sys.executable, "service.py"], env=env)
    except KeyboardInterrupt:
        print("\nService stopped.")

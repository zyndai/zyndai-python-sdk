"""zynd service — Service project scaffolding and unified run."""

import argparse
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
    generate_entity_id,
)
from zyndai_agent.dns_registry import (
    register_entity,
    get_entity,
    update_entity,
    get_entity_fqan,
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

    # zynd service run
    run_p = sub.add_parser(
        "run",
        help="Start the service, register/update it on the network, and keep running",
    )
    run_p.add_argument(
        "--config", default="service.config.json", help="Path to service.config.json"
    )
    run_p.add_argument("--port", type=int, help="Override webhook port")
    run_p.set_defaults(func=_service_run)

    p.set_defaults(func=_service_help)


def _service_help(args):
    print("Usage: zynd service {init,run}")
    print("  init   Create a new service project")
    print("  run    Start the service, register/update it on the network, and run")


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

    entity_name_zns = _to_zns_name(name)

    # Determine derivation index
    if args.index is not None:
        index = args.index
    else:
        svc_dir = services_dir()
        existing = list(svc_dir.iterdir()) if svc_dir.exists() else []
        index = len(existing)

    # Derive keypair
    svc_kp = derive_agent_keypair(dev_kp.private_key, index)
    svc_id = generate_entity_id(svc_kp.public_key_bytes, "service")

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

    # Write service.config.json with a minimal canonical schema. The core 11
    # fields (name, entity_name, description, category, tags, summary,
    # webhook_port, registry_url, keypair_path, entity_index, entity_pricing)
    # match agent.config.json byte-for-byte. service_endpoint and openapi_url
    # are the only service-specific fields — optional overrides for when the
    # service has a distinct API endpoint or publishes an OpenAPI spec.
    # Everything else that USED to live here is either derived at runtime
    # (entity_url from service_endpoint or webhook_port, price from
    # entity_pricing) or implicit (entity_type is always "service" here;
    # webhook_host is always "0.0.0.0" per the template default).
    config = {
        "name": name,
        "entity_name": entity_name_zns,
        "description": description,
        "category": category,
        "tags": [],
        "summary": "",
        "webhook_port": 5000,
        "service_endpoint": service_endpoint,
        "openapi_url": openapi_url,
        "registry_url": registry_url,
        "keypair_path": str(kp_path),
        "entity_index": index,
        "entity_pricing": None,
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
    print(f"    ZNS Name:   {entity_name_zns}")
    print(f"    Service ID: {svc_id}")
    print(f"    Keypair:    {kp_path}")
    print(f"\n  Next steps:")
    print(f"    1. Edit service.py with your service logic")
    print(f"    2. zynd service run   (registers on first run, updates on subsequent runs)")


def _service_run(args: argparse.Namespace):
    """Start the service, health-check it, upsert on the registry, and keep running."""
    import subprocess
    import time
    import requests as _req

    config_path = args.config
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.", file=sys.stderr)
        sys.exit(1)

    script_file = "service.py"
    if not os.path.exists(script_file):
        print(f"Error: {script_file} not found in current directory.", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    kp_path = config.get("keypair_path")
    if not kp_path or not os.path.exists(kp_path):
        print("Error: keypair_path not found in config.", file=sys.stderr)
        sys.exit(1)

    kp, meta = load_keypair_with_metadata(kp_path)
    service_id = generate_entity_id(kp.public_key_bytes, "service")
    registry_url = get_registry_url(getattr(args, "registry", None)) or config.get("registry_url", "http://localhost:8080")
    port = args.port or config.get("webhook_port", 5000)
    health_url = f"http://localhost:{port}/health"

    # entity_url is derived — it's not a first-class config field. Precedence:
    #   1. service_endpoint (config override, intended for distinct API hosts)
    #   2. http://localhost:<webhook_port> (the default local bind)
    # If you're running behind ngrok/proxy, set service_endpoint in the
    # config and both entity_url and service_endpoint will point at it.
    service_endpoint = config.get("service_endpoint")
    entity_url = service_endpoint or f"http://localhost:{port}"

    dev_path = developer_key_path()
    if not dev_path.exists():
        print("Error: Developer keypair not found.", file=sys.stderr)
        sys.exit(1)

    dev_kp = load_keypair(str(dev_path))
    dev_id = generate_developer_id(dev_kp.public_key_bytes)
    derived = (meta or {}).get("derived_from", {})
    entity_index = derived.get("index", config.get("entity_index", 0))
    proof = create_derivation_proof(dev_kp, kp.public_key, entity_index)
    entity_name_zns = config.get("entity_name", "")

    from rich.console import Console
    console = Console()

    # --- Step 1: Start the service as a background process ---
    console.print()
    console.print(f"  [bold #8B5CF6]▶[/bold #8B5CF6] Starting [bold]{config.get('name', 'service')}[/bold]...")

    env = os.environ.copy()
    env["ZYND_SERVICE_KEYPAIR_PATH"] = kp_path
    env["ZYND_REGISTRY_URL"] = registry_url
    if args.port:
        env["ZYND_WEBHOOK_PORT"] = str(args.port)

    proc = subprocess.Popen(
        [sys.executable, script_file],
        env=env,
        stdout=None,
        stderr=None,
    )

    # --- Step 2: Health-check ---
    console.print(f"  [dim]Waiting for service to start (health: {health_url})...[/dim]")
    healthy = False
    for _ in range(30):
        if proc.poll() is not None:
            console.print(f"  [bold red]✗[/bold red] Service process exited with code {proc.returncode}")
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
        console.print(f"  [bold red]✗[/bold red] Service did not become healthy within 15s")
        proc.terminate()
        sys.exit(1)

    console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Service is healthy")

    # --- Step 3: Upsert on registry ---
    existing = get_entity(registry_url, service_id, entity_type="service")

    if existing is not None:
        console.print(f"  [dim]Service already registered — updating...[/dim]")
        update_body = {
            "name": config["name"],
            "category": config.get("category", "general"),
            "tags": config.get("tags", []),
            "summary": config.get("summary", ""),
        }
        success = update_entity(
            registry_url, service_id, kp, update_body, entity_type="service"
        )
        if success:
            fqan = get_entity_fqan(registry_url, service_id)
            console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Service updated on registry")
            if fqan:
                console.print(f"  [dim]FQAN:[/dim]     [bold #F59E0B]{fqan}[/bold #F59E0B]")
        else:
            console.print(f"  [bold red]✗[/bold red] Update failed")
    else:
        try:
            service_id = register_entity(
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
                entity_type="service",
                service_endpoint=service_endpoint,
                openapi_url=config.get("openapi_url"),
                entity_pricing=config.get("entity_pricing"),
            )
            fqan = get_entity_fqan(registry_url, service_id)
            console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] Service registered: {service_id}")
            if fqan:
                console.print(f"  [dim]FQAN:[/dim]     [bold #F59E0B]{fqan}[/bold #F59E0B]")
            if entity_name_zns:
                console.print(f"  [dim]ZNS Name:[/dim] {entity_name_zns}")
        except Exception as e:
            console.print(f"  [bold red]✗[/bold red] Registration failed: {e}")

    # --- Step 4: Keep the service running ---
    console.print()
    console.print(f"  [bold green]Service is running.[/bold green] Press Ctrl+C to stop.")
    console.print()
    try:
        proc.wait()
    except KeyboardInterrupt:
        console.print(f"\n  [dim]Stopping service...[/dim]")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

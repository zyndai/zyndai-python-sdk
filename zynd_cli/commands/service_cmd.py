"""zynd service — Service project scaffolding and unified run."""

import argparse
import json
import os
import sys
from pathlib import Path

from zyndai_agent.ed25519_identity import (
    load_keypair,
    derive_agent_keypair,
    save_keypair,
    generate_entity_id,
)
from zynd_cli.config import (
    developer_key_path,
    services_dir,
    service_dir,
    service_keypair_path,
    ensure_zynd_dir,
    get_registry_url,
)
from zynd_cli.commands._entity_base import EntityRunner, slugify_name


class ServiceRunner(EntityRunner):
    """`zynd service run` — starts and upserts a service entity."""

    entity_type = "service"
    label = "Service"
    script_name = "service.py"
    config_name = "service.config.json"
    keypair_env = "ZYND_SERVICE_KEYPAIR_PATH"
    slug_suffix = "-service"

    def build_register_extras(self, config: dict, entity_url: str) -> dict:
        # The registry rejects service registration without a
        # service_endpoint. Default it to the local webhook URL so the
        # common single-host case just works; operators who front the
        # service with ngrok/a proxy override via config.service_endpoint.
        return {
            "service_endpoint": config.get("service_endpoint") or entity_url,
            "openapi_url": config.get("openapi_url"),
        }


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
    run_p.add_argument("--entity-url", help="Override entity URL for registration")
    run_p.set_defaults(func=_service_run)

    p.set_defaults(func=_service_help)


def _service_help(args):
    print("Usage: zynd service {init,run}")
    print("  init   Create a new service project")
    print("  run    Start the service, register/update it on the network, and run")


def _service_init(args: argparse.Namespace):
    """Scaffold a new service project."""
    ensure_zynd_dir()
    dev_path = developer_key_path()

    if not dev_path.exists():
        print("Error: Developer keypair not found.", file=sys.stderr)
        print("  Run 'zynd auth login --registry <url>' first.", file=sys.stderr)
        sys.exit(1)

    dev_kp = load_keypair(str(dev_path))

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

    entity_name_zns = slugify_name(name, "-service")

    if args.index is not None:
        index = args.index
    else:
        svc_dir = services_dir()
        existing = list(svc_dir.iterdir()) if svc_dir.exists() else []
        index = len(existing)

    svc_kp = derive_agent_keypair(dev_kp.private_key, index)
    svc_id = generate_entity_id(svc_kp.public_key_bytes, "service")

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

    # Minimal canonical schema. Deploy-config (keypair_path,
    # registry_url) lives in .env. Derivable fields (entity_url,
    # entity_type, webhook_host, price, entity_name) stay out —
    # entity_name is slugified from `name` at runtime and can still be
    # overridden by adding an explicit "entity_name" key.
    config = {
        "name": name,
        "description": description,
        "category": category,
        "tags": [],
        "summary": "",
        "webhook_port": 5000,
        "service_endpoint": service_endpoint,
        "openapi_url": openapi_url,
        "entity_index": index,
        "entity_pricing": None,
    }
    with open("service.config.json", "w") as f:
        json.dump(config, f, indent=2)

    env_lines = [
        f'ZYND_SERVICE_KEYPAIR_PATH="{kp_path}"',
        f'ZYND_REGISTRY_URL="{registry_url}"',
    ]
    with open(".env", "w") as f:
        f.write("\n".join(env_lines) + "\n")

    tpl_dir = Path(__file__).parent.parent / "templates"
    tpl_path = tpl_dir / "service.py.tpl"
    if tpl_path.exists():
        content = tpl_path.read_text().replace("__SERVICE_NAME__", name)
        with open("service.py", "w") as f:
            f.write(content)

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
    """Delegate to the shared EntityRunner base class."""
    ServiceRunner().run(args)

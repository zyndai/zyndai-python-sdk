"""Shared base class for `zynd agent run` and `zynd service run`.

Both commands share a nearly identical run flow: load .env, read config,
resolve a keypair, spawn the user script, health-check it, upsert on the
registry, then wait. This module centralizes that flow so single-line
fixes (e.g. the load_dotenv cwd bug) don't have to be applied twice.

Subclasses declare class attributes and override a few hook methods.
See AgentRunner / ServiceRunner in agent_cmd.py / service_cmd.py.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from zyndai_agent.ed25519_identity import (
    load_keypair,
    load_keypair_with_metadata,
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
    get_registry_url,
)


def slugify_name(name: str, short_suffix: str = "") -> str:
    """Convert a free-form display name into a ZNS-safe slug.

    Lowercase, spaces/underscores -> hyphens, drop anything that isn't
    alphanumeric or a hyphen, collapse repeated hyphens, trim the ends.
    If the result is shorter than 3 characters we pad it with
    ``short_suffix`` (e.g. ``"-agent"`` / ``"-service"``).
    """
    slug = re.sub(
        r"[^a-z0-9-]", "", name.lower().replace(" ", "-").replace("_", "-")
    )
    slug = re.sub(r"-+", "-", slug).strip("-")
    if len(slug) < 3:
        slug = slug + short_suffix
    if len(slug) > 36:
        slug = slug[:36]
    return slug


def load_cwd_env() -> None:
    """Load ``.env`` from the current working directory.

    ``load_dotenv()`` with no args walks up from the *calling file* (the
    installed ``zynd_cli`` package), so it never finds the project-local
    file. We pass an explicit cwd path instead.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=Path.cwd() / ".env")
    except ImportError:
        pass


class EntityRunner:
    """Base class implementing the `zynd <kind> run` lifecycle.

    Subclasses must set ``entity_type``, ``label``, ``script_name``,
    ``config_name``, and ``keypair_env``. They may override the hook
    methods ``pre_register``, ``build_update_body``, and
    ``build_register_extras`` to add kind-specific behavior.
    """

    # --- subclass config ---
    entity_type: str = "agent"
    label: str = "Agent"
    script_name: str = "agent.py"
    config_name: str = "agent.config.json"
    keypair_env: str = "ZYND_AGENT_KEYPAIR_PATH"
    slug_suffix: str = ""

    # ------------------------------------------------------------------
    # Hooks — override in subclass
    # ------------------------------------------------------------------

    def pre_register(
        self,
        config: dict,
        kp,
        registry_url: str,
        dev_id: str,
        entity_id: str,
        console,
    ) -> Optional[str]:
        """Called after health check, before register/update.

        Default returns the slugified entity name (or an explicit
        ``config["entity_name"]`` override). Subclasses may extend to do
        availability checks or print warnings.
        """
        return config.get("entity_name") or slugify_name(
            config.get("name", ""), self.slug_suffix
        )

    def build_update_body(self, config: dict, entity_url: str) -> dict:
        """Body dict passed to ``update_entity`` when the entity already
        exists on the registry. Subclasses extend with kind-specific
        fields.
        """
        return {
            "name": config["name"],
            "category": config.get("category", "general"),
            "tags": config.get("tags", []),
            "summary": config.get("summary", ""),
        }

    def build_register_extras(
        self, config: dict, entity_url: str
    ) -> dict:
        """Extra kwargs passed to ``register_entity`` for first-time
        registration. Subclasses add kind-specific fields here (e.g.
        ``service_endpoint`` / ``openapi_url``).
        """
        return {}

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    def run(self, args) -> None:
        load_cwd_env()

        config_path = getattr(args, "config", None) or self.config_name
        if not os.path.exists(config_path):
            print(f"Error: {config_path} not found.", file=sys.stderr)
            sys.exit(1)

        if not os.path.exists(self.script_name):
            print(
                f"Error: {self.script_name} not found in current directory.",
                file=sys.stderr,
            )
            sys.exit(1)

        with open(config_path) as f:
            config = json.load(f)

        kp_path = self._resolve_keypair_path(config)
        kp, meta = load_keypair_with_metadata(kp_path)

        registry_url = get_registry_url(getattr(args, "registry", None))
        port = getattr(args, "port", None) or config.get("webhook_port", 5000)
        entity_url = (
            getattr(args, "entity_url", None) or f"http://localhost:{port}"
        )
        health_url = f"http://localhost:{port}/health"

        dev_path = developer_key_path()
        if not dev_path.exists():
            print("Error: Developer keypair not found.", file=sys.stderr)
            sys.exit(1)

        dev_kp = load_keypair(str(dev_path))
        dev_id = generate_developer_id(dev_kp.public_key_bytes)
        derived = (meta or {}).get("derived_from", {})
        entity_index = derived.get("index", config.get("entity_index", 0))
        proof = create_derivation_proof(dev_kp, kp.public_key, entity_index)

        entity_id = generate_entity_id(kp.public_key_bytes, self.entity_type)

        from rich.console import Console

        console = Console()

        # --- Step 1: spawn the user script as a subprocess -----------
        console.print()
        console.print(
            f"  [bold #8B5CF6]▶[/bold #8B5CF6] Starting "
            f"[bold]{config.get('name', self.label.lower())}[/bold]..."
        )
        proc = self._spawn(kp_path, registry_url, getattr(args, "port", None))

        # --- Step 2: health check ------------------------------------
        console.print(
            f"  [dim]Waiting for {self.label.lower()} to start "
            f"(health: {health_url})...[/dim]"
        )
        if not self._health_check(proc, health_url, console):
            console.print(
                f"  [bold red]✗[/bold red] {self.label} did not become "
                f"healthy within 15s"
            )
            proc.terminate()
            sys.exit(1)
        console.print(
            f"  [bold #8B5CF6]✓[/bold #8B5CF6] {self.label} is healthy"
        )

        # --- Step 3: pre-register hook --------------------------------
        try:
            entity_name_zns = self.pre_register(
                config, kp, registry_url, dev_id, entity_id, console
            )
        except SystemExit:
            proc.terminate()
            raise

        # --- Step 4: upsert on registry -------------------------------
        existing = get_entity(
            registry_url, entity_id, entity_type=self.entity_type
        )
        if existing is not None:
            console.print(
                f"  [dim]{self.label} already registered — updating...[/dim]"
            )
            update_body = self.build_update_body(config, entity_url)
            ok = update_entity(
                registry_url,
                entity_id,
                kp,
                update_body,
                entity_type=self.entity_type,
            )
            if ok:
                fqan = get_entity_fqan(registry_url, entity_id)
                console.print(
                    f"  [bold #8B5CF6]✓[/bold #8B5CF6] {self.label} "
                    f"updated on registry"
                )
                if fqan:
                    console.print(
                        f"  [dim]FQAN:[/dim]     "
                        f"[bold #F59E0B]{fqan}[/bold #F59E0B]"
                    )
            else:
                console.print(f"  [bold red]✗[/bold red] Update failed")
        else:
            try:
                extras = self.build_register_extras(config, entity_url)
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
                    entity_type=self.entity_type,
                    entity_pricing=config.get("entity_pricing"),
                    **extras,
                )
                fqan = get_entity_fqan(registry_url, entity_id)
                console.print(
                    f"  [bold #8B5CF6]✓[/bold #8B5CF6] {self.label} "
                    f"registered: {entity_id}"
                )
                if fqan:
                    console.print(
                        f"  [dim]FQAN:[/dim]     "
                        f"[bold #F59E0B]{fqan}[/bold #F59E0B]"
                    )
                if entity_name_zns:
                    console.print(
                        f"  [dim]ZNS Name:[/dim] {entity_name_zns}"
                    )
            except Exception as e:
                console.print(
                    f"  [bold red]✗[/bold red] Registration failed: {e}"
                )
                console.print(
                    f"  [dim]⚠ Entity is running locally but NOT discoverable on the network[/dim]"
                )

        # --- Step 5: wait for exit -----------------------------------
        console.print()
        console.print(
            f"  [bold green]{self.label} is running.[/bold green] "
            f"Press Ctrl+C to stop."
        )
        console.print()
        try:
            proc.wait()
        except KeyboardInterrupt:
            console.print(
                f"\n  [dim]Stopping {self.label.lower()}...[/dim]"
            )
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_keypair_path(self, config: dict) -> str:
        path = os.environ.get(self.keypair_env) or config.get("keypair_path")
        if not path:
            print(
                f"Error: {self.label.lower()} keypair path not set. "
                f"Export {self.keypair_env} or add it to .env in the "
                f"current directory.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not os.path.exists(path):
            print(
                f"Error: {self.label.lower()} keypair not found at {path}",
                file=sys.stderr,
            )
            sys.exit(1)
        return path

    def _spawn(
        self,
        kp_path: str,
        registry_url: str,
        port_override: Optional[int],
    ) -> subprocess.Popen:
        env = os.environ.copy()
        env[self.keypair_env] = kp_path
        env["ZYND_REGISTRY_URL"] = registry_url
        if port_override:
            env["ZYND_WEBHOOK_PORT"] = str(port_override)
        return subprocess.Popen(
            [sys.executable, self.script_name],
            env=env,
            stdout=None,
            stderr=None,
        )

    def _health_check(
        self, proc: subprocess.Popen, health_url: str, console
    ) -> bool:
        import requests as _req

        for _ in range(30):
            if proc.poll() is not None:
                console.print(
                    f"  [bold red]✗[/bold red] {self.label} process "
                    f"exited with code {proc.returncode}"
                )
                return False
            try:
                resp = _req.get(health_url, timeout=1)
                if resp.status_code == 200:
                    return True
            except _req.ConnectionError:
                pass
            time.sleep(0.5)
        return False

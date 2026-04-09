"""zynd CLI entry point."""

import argparse
import sys

from zynd_cli import __version__
from zynd_cli.commands import (
    agent_cmd,
    service_cmd,
    auth,
    info,
    init_cmd,
    search,
    resolve,
    card,
    deregister,
    keys,
    status,
)

# Shared parent parser with --registry flag — inherited by all subcommands
_registry_parent = argparse.ArgumentParser(add_help=False)
_registry_parent.add_argument(
    "--registry",
    help="Registry URL (overrides env/config)",
    default=None,
)


def main():
    parser = argparse.ArgumentParser(
        prog="zynd",
        description="Developer CLI for the Zynd AI Agent Network",
        parents=[_registry_parent],
    )
    parser.add_argument("--version", action="version", version=f"zynd {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    # Register all subcommands
    agent_cmd.register_parser(subparsers, parents=[_registry_parent])
    service_cmd.register_parser(subparsers, parents=[_registry_parent])
    auth.register_parser(subparsers)
    info.register_parser(subparsers, parents=[_registry_parent])
    init_cmd.register_parser(subparsers)
    search.register_parser(subparsers, parents=[_registry_parent])
    resolve.register_parser(subparsers, parents=[_registry_parent])
    card.register_parser(subparsers, parents=[_registry_parent])
    deregister.register_parser(subparsers, parents=[_registry_parent])
    keys.register_parser(subparsers)
    status.register_parser(subparsers, parents=[_registry_parent])

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if not hasattr(args, "func"):
        parser.parse_args([args.command, "--help"])
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()

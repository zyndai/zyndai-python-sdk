"""zynd CLI entry point."""

import argparse
import sys

from zynd_cli import __version__
from zynd_cli.commands import (
    auth,
    init_cmd,
    register,
    search,
    resolve,
    card,
    deregister,
    keys,
    status,
)


def main():
    parser = argparse.ArgumentParser(
        prog="zynd",
        description="Developer CLI for the Zynd AI Agent Network",
    )
    parser.add_argument("--version", action="version", version=f"zynd {__version__}")
    parser.add_argument(
        "--registry",
        help="Registry URL (overrides env/config)",
        default=None,
    )

    subparsers = parser.add_subparsers(dest="command")

    # Register all subcommands
    auth.register_parser(subparsers)
    init_cmd.register_parser(subparsers)
    register.register_parser(subparsers)
    search.register_parser(subparsers)
    resolve.register_parser(subparsers)
    card.register_parser(subparsers)
    deregister.register_parser(subparsers)
    keys.register_parser(subparsers)
    status.register_parser(subparsers)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if not hasattr(args, "func"):
        # Subcommand group (e.g. "zynd auth") without a specific subcommand
        parser.parse_args([args.command, "--help"])
        sys.exit(0)

    # Pass registry flag down to commands
    args.func(args)


if __name__ == "__main__":
    main()

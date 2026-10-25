"""Command line entry point: `cldf-mcp [serve|setup|info]`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .paths import ENV_CONFIG, default_data_dir


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"where data and the database are kept (default: {default_data_dir()}, or $CLDF_MCP_DATA_DIR)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=os.environ.get(ENV_CONFIG) or None,
        help="extra datasets TOML to add or override datasets (or $CLDF_MCP_CONFIG)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cldf-mcp", description="MCP server for CLDF linguistic datasets.")
    parser.add_argument("--version", action="version", version=f"cldf-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP server (default)")
    _common(serve)
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    setup = sub.add_parser("setup", help="download the pinned datasets and build the database")
    _common(setup)
    setup.add_argument("--force", action="store_true", help="rebuild even if the database is current")

    info = sub.add_parser("info", help="show where data lives and what is loaded")
    _common(info)

    # `cldf-mcp` with no subcommand (how MCP clients launch it) means `serve` over stdio.
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("serve", "setup", "info", "-h", "--help", "--version"):
        argv = ["serve", *argv]
    args = parser.parse_args(argv)

    from .server import DataManager, create_server

    manager = DataManager(args.data_dir, args.config)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="cldf-mcp: %(message)s")

    if args.command == "setup":
        manager.prepare(force=args.force)
        print(json.dumps(manager.store().list_datasets(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "info":
        from .build import is_current
        from .registry import load_registry

        specs = load_registry(args.config)
        print(
            json.dumps(
                {
                    "version": __version__,
                    "data_dir": str(manager.data_dir),
                    "database": str(manager.db),
                    "database_current": is_current(manager.db, specs),
                    "datasets": {k: s.version for k, s in specs.items()},
                },
                indent=2,
            )
        )
        return 0

    mcp = create_server(manager)
    manager.warm_up()  # build/load in the background so the first tool call is fast
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

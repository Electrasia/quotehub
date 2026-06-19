#!/usr/bin/env python3
"""
backend/cli.py — QuoteHub CLI entry point for operations outside the app.

Usage:
    python -m backend.cli backup pre-update --version 1.4.2
    python -m backend.cli key rotate

Commands:
    backup pre-update --version VERSION
                          Create a pre-update auto-backup.
    key rotate            Rotate the Internal Backup Key.
    key current           Show the current key version.
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure the backend package is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.auto_backup import (
    EVENTS_DIR,
    _ensure_dirs,
    run_event_backup,
)
from backend.key_manager import (
    ensure_internal_key,
    get_current_key_version,
    rotate_internal_key,
)

logger = logging.getLogger(__name__)


def cmd_backup_pre_update(args: argparse.Namespace) -> int:
    """Create a pre-update auto-backup for the given target version.

    Idempotent: if a pre-update backup for this version already exists
    (created within the last 5 minutes), exits 0 without duplicating.
    """
    version = args.version
    tag = f"pre-update-v{version}"

    # Idempotency check
    _ensure_dirs()
    existing = list(EVENTS_DIR.glob(f"{tag}_*.quodb"))
    if existing:
        print(f"OK  Pre-update backup already exists: {existing[-1].name}")
        return 0

    try:
        ensure_internal_key()
        result = run_event_backup(tag)
        print(f"OK  Pre-update backup created: {Path(result['packagePath']).name} "
              f"({result['packageSizeBytes'] / 1_048_576:.1f} MB)")
        return 0
    except Exception as e:
        print(f"FAILED Pre-update backup: {e}", file=sys.stderr)
        logger.exception("CLI pre-update backup FAILED (version=%s)", version)
        return 1


def cmd_key_rotate(_args: argparse.Namespace) -> int:
    """Rotate the Internal Backup Key."""
    try:
        ensure_internal_key()
        new_ver = rotate_internal_key()
        print(f"OK  Key rotated to v{new_ver}")
        return 0
    except Exception as e:
        print(f"FAILED Key rotation: {e}", file=sys.stderr)
        logger.exception("CLI key rotation FAILED")
        return 1


def cmd_key_current(_args: argparse.Namespace) -> int:
    """Show the current key version."""
    try:
        ensure_internal_key()
        v = get_current_key_version()
        print(f"v{v}")
        return 0
    except Exception as e:
        print(f"FAILED {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quodb-cli",
        description="QuoteHub CLI — backup and key management",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backup
    bp = sub.add_parser("backup", help="Backup operations")
    bsub = bp.add_subparsers(dest="subcommand", required=True)
    pre_up = bsub.add_parser("pre-update", help="Create a pre-update backup")
    pre_up.add_argument("--version", required=True, help="Target app version")

    # key
    kp = sub.add_parser("key", help="Internal Backup Key operations")
    ksub = kp.add_subparsers(dest="subcommand", required=True)
    ksub.add_parser("rotate", help="Rotate to a new key version")
    ksub.add_parser("current", help="Show current key version")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Route to handler
    if args.command == "backup":
        if args.subcommand == "pre-update":
            return cmd_backup_pre_update(args)
    elif args.command == "key":
        if args.subcommand == "rotate":
            return cmd_key_rotate(args)
        elif args.subcommand == "current":
            return cmd_key_current(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

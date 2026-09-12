"""Synchronize the shared identity module into explicitly named vendored packages."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-package", type=Path, action="append", required=True)
    parser.add_argument("--check", action="store_true", help="Check byte equality without writing")
    args = parser.parse_args()
    source = (
        Path(__file__).resolve().parent.parent / "packages/mcp-common/src/mcp_common/identity.py"
    ).read_bytes()
    failed = False
    for package in args.target_package:
        if not package.is_dir() or not (package / "__init__.py").is_file():
            parser.error(f"Not an existing Python package: {package}")
        target = package / "identity.py"
        if args.check:
            if not target.is_file() or target.read_bytes() != source:
                print(f"Identity source drift: {target}")
                failed = True
        else:
            target.write_bytes(source)
            print(f"Synced identity: {target}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

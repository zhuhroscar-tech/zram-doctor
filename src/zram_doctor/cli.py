"""zram-doctor CLI."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import __version__
from .core import collect_and_evaluate


LEVEL_LABEL = {"fail": "FAIL", "warn": "WARN", "info": "info"}


def _print_human(report) -> None:
    print(f"Configured zram devices: {', '.join(c.name for c in report.configured) or 'none'}")
    print(f"Live zram devices:       {', '.join(d.name for d in report.live) or 'none'}")
    print()
    for f in report.findings:
        print(f"[{LEVEL_LABEL.get(f.level, f.level.upper())}] {f.message}")
    print()
    if report.has_failures:
        print("Result: drift detected -- a configured device failed to come up.")
    elif report.has_warnings:
        print("Result: drift detected -- config and running state disagree (see WARN above).")
    else:
        print("Result: no drift detected.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zram-doctor",
        description=(
            "Detects drift between zram-generator.conf and the actually-running "
            "zram device(s) -- e.g. a config change that was never applied because "
            "the systemd-zram-setup unit was never restarted. Read-only."
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect_and_evaluate()

    if args.json:
        out = {
            "configured": [asdict(c) for c in report.configured],
            "live": [asdict(d) for d in report.live],
            "findings": [asdict(f) for f in report.findings],
            "has_failures": report.has_failures,
            "has_warnings": report.has_warnings,
        }
        print(json.dumps(out, indent=2))
    else:
        _print_human(report)

    if report.has_failures:
        return 2
    if report.has_warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

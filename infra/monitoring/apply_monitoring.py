#!/usr/bin/env python3
"""Apply monitoring dashboards and alert policies using gcloud."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
DASHBOARD_FILES = sorted(ROOT.glob("dashboard_*.json"))
ALERT_FILES = sorted(ROOT.glob("alert_*.json"))


def _run_gcloud(
    args: list[str],
    *,
    project: str | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["gcloud"] + args
    if project:
        cmd.extend(["--project", project])
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture,
    )


def _lookup_resource(
    kind: str,
    display_name: str,
    *,
    project: str | None = None,
) -> str | None:
    if kind == "dashboard":
        args = [
            "monitoring",
            "dashboards",
            "list",
            "--filter",
            f'displayName="{display_name}"',
            "--format",
            "value(name)",
        ]
    else:
        args = [
            "alpha",
            "monitoring",
            "policies",
            "list",
            "--filter",
            f'displayName="{display_name}"',
            "--format",
            "value(name)",
        ]
    result = _run_gcloud(args, project=project, capture=True)
    result.check_returncode()
    rid = result.stdout.strip()
    return rid.splitlines()[0] if rid else None


def _apply_dashboards(project: str | None = None) -> None:
    for path in DASHBOARD_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        display = data.get("displayName", path.name)
        existing = _lookup_resource("dashboard", display, project=project)
        if existing:
            print(f"Updating dashboard '{display}' ({existing}) from {path.name}")
            result = _run_gcloud(
                [
                    "monitoring",
                    "dashboards",
                    "update",
                    existing,
                    "--config-from-file",
                    str(path),
                ],
                project=project,
            )
        else:
            print(f"Creating dashboard '{display}' from {path.name}")
            result = _run_gcloud(
                [
                    "monitoring",
                    "dashboards",
                    "create",
                    "--config-from-file",
                    str(path),
                ],
                project=project,
            )
        result.check_returncode()


def _apply_alerts(project: str | None = None) -> None:
    for path in ALERT_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        display = data.get("displayName", path.name)
        existing = _lookup_resource("alert", display, project=project)
        if existing:
            print(f"Updating alert policy '{display}' ({existing}) from {path.name}")
            result = _run_gcloud(
                [
                    "alpha",
                    "monitoring",
                    "policies",
                    "update",
                    existing,
                    "--policy-from-file",
                    str(path),
                ],
                project=project,
            )
        else:
            print(f"Creating alert policy '{display}' from {path.name}")
            result = _run_gcloud(
                [
                    "alpha",
                    "monitoring",
                    "policies",
                    "create",
                    "--policy-from-file",
                    str(path),
                ],
                project=project,
            )
        result.check_returncode()


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        help="Optional GCP project ID. Falls back to gcloud's active project when omitted.",
    )
    args = parser.parse_args(argv)
    if not DASHBOARD_FILES and not ALERT_FILES:
        parser.error("No dashboard or alert JSON files found under infra/monitoring")
    _apply_dashboards(project=args.project)
    _apply_alerts(project=args.project)
    print("Monitoring assets applied successfully.")


if __name__ == "__main__":
    main()

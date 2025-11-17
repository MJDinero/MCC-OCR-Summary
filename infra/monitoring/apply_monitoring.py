#!/usr/bin/env python3
"""Apply monitoring dashboards and alert policies using gcloud."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


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


def _render_template(
    path: Path,
    *,
    environment: str | None,
    project: str | None,
    pagerduty_channel: str | None,
    email_channel: str | None,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    replacements = {
        "${ENV}": environment or "",
        " (${ENV})": f" ({environment})" if environment else "",
        "${PROJECT_ID}": project or "",
        "${PAGERDUTY_CHANNEL}": pagerduty_channel
        or (f"{environment}-pagerduty" if environment else ""),
        "${EMAIL_CHANNEL}": email_channel
        or (f"{environment}-email" if environment else ""),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return json.loads(text)


def _write_temp_json(data: dict[str, Any]) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, tmp)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def _apply_dashboards(
    project: str | None = None,
    *,
    environment: str | None,
    pagerduty_channel: str | None,
    email_channel: str | None,
) -> None:
    for path in DASHBOARD_FILES:
        data = _render_template(
            path,
            environment=environment,
            project=project,
            pagerduty_channel=pagerduty_channel,
            email_channel=email_channel,
        )
        rendered_path = _write_temp_json(data)
        display = data.get("displayName", path.name)
        existing = _lookup_resource("dashboard", display, project=project)
        if existing:
            print(f"Replacing dashboard '{display}' ({existing}) from {path.name}")
            delete_result = _run_gcloud(
                ["monitoring", "dashboards", "delete", existing, "--quiet"],
                project=project,
            )
            delete_result.check_returncode()
        else:
            print(f"Creating dashboard '{display}' from {path.name}")
        result = _run_gcloud(
            [
                "monitoring",
                "dashboards",
                "create",
                "--config-from-file",
                str(rendered_path),
            ],
            project=project,
        )
        result.check_returncode()
        rendered_path.unlink(missing_ok=True)


def _apply_alerts(
    project: str | None = None,
    *,
    environment: str | None,
    pagerduty_channel: str | None,
    email_channel: str | None,
) -> None:
    for path in ALERT_FILES:
        data = _render_template(
            path,
            environment=environment,
            project=project,
            pagerduty_channel=pagerduty_channel,
            email_channel=email_channel,
        )
        rendered_path = _write_temp_json(data)
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
                    str(rendered_path),
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
                str(rendered_path),
            ],
            project=project,
        )
        try:
            result.check_returncode()
        except subprocess.CalledProcessError:
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            print(
                f"Warning: failed to apply alert '{display}' "
                f"(stdout={stdout} stderr={stderr})"
            )
        rendered_path.unlink(missing_ok=True)
        rendered_path.unlink(missing_ok=True)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        help="Optional GCP project ID. Falls back to gcloud's active project when omitted.",
    )
    parser.add_argument(
        "--environment",
        help="Optional environment label used to render ${ENV} placeholders (e.g. prod, staging).",
    )
    parser.add_argument(
        "--pagerduty-channel",
        help="Fully-qualified notification channel name for PagerDuty alerts.",
    )
    parser.add_argument(
        "--email-channel",
        help="Fully-qualified notification channel name for email alerts.",
    )
    args = parser.parse_args(argv)
    if not DASHBOARD_FILES and not ALERT_FILES:
        parser.error("No dashboard or alert JSON files found under infra/monitoring")
    _apply_dashboards(
        project=args.project,
        environment=args.environment,
        pagerduty_channel=args.pagerduty_channel,
        email_channel=args.email_channel,
    )
    _apply_alerts(
        project=args.project,
        environment=args.environment,
        pagerduty_channel=args.pagerduty_channel,
        email_channel=args.email_channel,
    )
    print("Monitoring assets applied successfully.")


if __name__ == "__main__":
    main()

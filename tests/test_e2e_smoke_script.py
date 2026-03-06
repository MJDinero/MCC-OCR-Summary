from pathlib import Path
import subprocess


SCRIPT_PATH = Path("scripts/e2e_smoke.sh")


def test_e2e_smoke_dry_run_prints_plan() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "DRY RUN ONLY" in result.stdout
    assert "NON-PHI" in result.stdout
    assert "gcloud scheduler jobs run" in result.stdout
    assert "gcloud workflows executions list" in result.stdout
    assert "gs://mcc-output/summaries/<job_id>.json" in result.stdout
    assert "gs://mcc-output/pdf/<job_id>.pdf" in result.stdout


def test_e2e_smoke_enforces_live_run_guard() -> None:
    script_text = SCRIPT_PATH.read_text()
    assert "CONFIRM_LIVE_RUN" in script_text
    assert "Blocked: set CONFIRM_LIVE_RUN=1 to run cloud actions." in script_text

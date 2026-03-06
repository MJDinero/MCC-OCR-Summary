import pathlib

import yaml


def _cloudbuild_env_map() -> tuple[dict[str, str], dict[str, str]]:
    doc = yaml.safe_load(pathlib.Path("cloudbuild.yaml").read_text())
    deploy_step = next(
        step for step in doc["steps"] if step["name"] == "gcr.io/cloud-builders/gcloud"
    )
    set_env_arg = next(
        arg for arg in deploy_step["args"] if arg.startswith("--set-env-vars=")
    )
    update_secrets_arg = next(
        arg for arg in deploy_step["args"] if arg.startswith("--update-secrets=")
    )

    env_map = {}
    for pair in set_env_arg.removeprefix("--set-env-vars=").split(","):
        key, _, value = pair.partition("=")
        env_map[key] = value

    secret_map = {}
    for pair in update_secrets_arg.removeprefix("--update-secrets=").split(","):
        key, _, value = pair.partition("=")
        secret_map[key] = value

    return env_map, secret_map


def _collect_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        collected = []
        for item in value:
            collected.extend(_collect_strings(item))
        return collected
    if isinstance(value, dict):
        collected = []
        for item in value.values():
            collected.extend(_collect_strings(item))
        return collected
    return []


def test_pipeline_manifest_marked_legacy_reference_only():
    doc = yaml.safe_load(pathlib.Path("pipeline.yaml").read_text())
    annotations = doc.get("metadata", {}).get("annotations", {})
    assert annotations.get("mcc.dev/manifest-status") == "legacy-reference-only"
    assert annotations.get("mcc.dev/authoritative-deploy") == "cloudbuild.yaml"


def test_gmp_sidecar_present():
    doc = yaml.safe_load(pathlib.Path("pipeline.yaml").read_text())
    containers = doc["spec"]["template"]["spec"].get("containers", [])
    names = {container["name"] for container in containers}
    assert "gmp-sidecar" in names
    sidecar = next(
        container for container in containers if container["name"] == "gmp-sidecar"
    )
    env = {item["name"]: item["value"] for item in sidecar.get("env", [])}
    assert env["TARGET"].endswith("/metrics")
    assert env["PROJECT_ID"] == "quantify-agent"


def test_cloudbuild_sets_fail_closed_pipeline_env_vars():
    doc = yaml.safe_load(pathlib.Path("cloudbuild.yaml").read_text())
    env_map, secret_map = _cloudbuild_env_map()

    assert env_map["PIPELINE_STATE_BACKEND"] == "gcs"
    assert env_map["PIPELINE_STATE_BUCKET"] == "$_PIPELINE_STATE_BUCKET"
    assert env_map["PIPELINE_STATE_PREFIX"] == "pipeline-state"
    assert (
        env_map["PIPELINE_WORKFLOW_NAME"]
        == "projects/$_PROJECT_ID/locations/$_REGION/workflows/docai-pipeline"
    )
    assert env_map["PIPELINE_SERVICE_BASE_URL"] == "$_PIPELINE_SERVICE_BASE_URL"
    assert env_map["SUMMARISER_JOB_NAME"] == "$_SUMMARISER_JOB_NAME"
    assert env_map["PDF_JOB_NAME"] == "$_PDF_JOB_NAME"
    assert env_map["PROJECT_ID"] == "$_PROJECT_ID"
    assert env_map["REGION"] == "$_REGION"

    required_runtime_keys = {
        "PIPELINE_SERVICE_BASE_URL",
        "SUMMARISER_JOB_NAME",
        "PDF_JOB_NAME",
        "PROJECT_ID",
        "REGION",
        "INTAKE_GCS_BUCKET",
        "OUTPUT_GCS_BUCKET",
        "SUMMARY_BUCKET",
        "DOC_AI_LOCATION",
        "DOC_AI_PROCESSOR_ID",
    }
    assert required_runtime_keys.issubset(env_map)
    assert all(env_map[key] != "" for key in required_runtime_keys)
    assert "INTERNAL_EVENT_TOKEN" in secret_map

    substitutions = doc.get("substitutions", {})
    assert substitutions["_PIPELINE_SERVICE_BASE_URL"]
    assert substitutions["_SUMMARISER_JOB_NAME"]
    assert substitutions["_PDF_JOB_NAME"]


def test_workflow_internal_event_callbacks_use_ingest_prefix():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    lines = workflow_text.splitlines()
    callback_lines = [
        line for line in lines if "url:" in line and "internal/jobs/" in line
    ]

    assert callback_lines
    assert all(
        '"/ingest/internal/jobs/" + jobId + "/events"' in line
        for line in callback_lines
    )


def test_workflow_yaml_is_parseable():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    workflow_doc = yaml.safe_load(workflow_text)
    assert isinstance(workflow_doc, dict)
    assert workflow_doc["main"]["steps"]


def test_workflow_artifact_paths_are_deterministic():
    workflow_doc = yaml.safe_load(pathlib.Path("workflows/pipeline.yaml").read_text())
    all_strings = _collect_strings(workflow_doc)
    summary_paths = [
        value for value in all_strings if "/summaries/" in value and ".json" in value
    ]
    pdf_paths = [value for value in all_strings if "/pdf/" in value and ".pdf" in value]

    assert summary_paths
    assert pdf_paths
    assert all('"/summaries/" + jobId + ".json"' in value for value in summary_paths)
    assert all('"/pdf/" + jobId + ".pdf"' in value for value in pdf_paths)

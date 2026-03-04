import pathlib

import yaml


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
    deploy_step = next(
        step for step in doc["steps"] if step["name"] == "gcr.io/cloud-builders/gcloud"
    )
    set_env_arg = next(
        arg for arg in deploy_step["args"] if arg.startswith("--set-env-vars=")
    )
    pairs = set_env_arg.removeprefix("--set-env-vars=").split(",")
    env_map = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        env_map[key] = value

    assert env_map["PIPELINE_STATE_BACKEND"] == "gcs"
    assert env_map["PIPELINE_STATE_BUCKET"] == "$_PIPELINE_STATE_BUCKET"
    assert env_map["PIPELINE_STATE_PREFIX"] == "pipeline-state"
    assert (
        env_map["PIPELINE_WORKFLOW_NAME"]
        == "projects/$_PROJECT_ID/locations/$_REGION/workflows/docai-pipeline"
    )

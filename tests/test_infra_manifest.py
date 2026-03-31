import pathlib

import yaml


def _cloudbuild_doc() -> dict:
    return yaml.safe_load(pathlib.Path("cloudbuild.yaml").read_text())


def _cloudbuild_gcloud_step(*prefix: str) -> dict:
    return next(
        step
        for step in _cloudbuild_doc()["steps"]
        if step["name"] == "gcr.io/cloud-builders/gcloud"
        and step["args"][: len(prefix)] == list(prefix)
    )


def _parse_arg_pairs(args: list[str], prefix: str) -> dict[str, str]:
    payload = next(arg for arg in args if arg.startswith(prefix))
    entries = {}
    for pair in payload.removeprefix(prefix).split(","):
        key, _, value = pair.partition("=")
        entries[key] = value
    return entries


def _cloudbuild_env_map() -> tuple[dict[str, str], dict[str, str]]:
    deploy_step = _cloudbuild_gcloud_step("run", "deploy")
    env_map = _parse_arg_pairs(deploy_step["args"], "--set-env-vars=")
    secret_map = _parse_arg_pairs(deploy_step["args"], "--update-secrets=")
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
    doc = _cloudbuild_doc()
    env_map, secret_map = _cloudbuild_env_map()

    assert env_map["PIPELINE_STATE_BACKEND"] == "gcs"
    assert env_map["PIPELINE_STATE_BUCKET"] == "$_PIPELINE_STATE_BUCKET"
    assert env_map["PIPELINE_STATE_PREFIX"] == "pipeline-state"
    assert (
        env_map["PIPELINE_WORKFLOW_NAME"]
        == "projects/$_PROJECT_ID/locations/$_REGION/workflows/$_WORKFLOW_NAME"
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
        "SUMMARY_STRATEGY",
        "SUMMARY_CHUNKED_MODEL",
        "SUMMARY_ONE_SHOT_MODEL",
        "SUMMARY_ONE_SHOT_REASONING_EFFORT",
        "SUMMARY_ONE_SHOT_TOKEN_THRESHOLD",
        "SUMMARY_ONE_SHOT_MAX_PAGES",
        "SUMMARY_OCR_NOISE_RATIO_THRESHOLD",
        "SUMMARY_NATIVE_TEXT_MIN_CHARS",
        "SUMMARY_NATIVE_TEXT_MIN_PAGE_RATIO",
        "SUMMARY_NATIVE_TEXT_MIN_AVG_PAGE_CHARS",
        "SUMMARY_NATIVE_TEXT_MIN_ALPHA_RATIO",
        "SUMMARY_NATIVE_TEXT_MAX_SHORT_PAGE_RATIO",
    }
    assert required_runtime_keys.issubset(env_map)
    assert all(env_map[key] != "" for key in required_runtime_keys)
    assert "INTERNAL_EVENT_TOKEN" in secret_map
    assert env_map["SUMMARY_STRATEGY"] == "auto"
    assert env_map["SUMMARY_CHUNKED_MODEL"] == "gpt-4.1-mini"
    assert env_map["SUMMARY_ONE_SHOT_MODEL"] == "gpt-5.4"
    assert env_map["SUMMARY_ONE_SHOT_REASONING_EFFORT"] == "none"

    substitutions = doc.get("substitutions", {})
    assert substitutions["_PIPELINE_SERVICE_BASE_URL"]
    assert substitutions["_SUMMARISER_JOB_NAME"]
    assert substitutions["_PDF_JOB_NAME"]
    assert substitutions["_JOB_SERVICE_ACCOUNT"]
    assert substitutions["_WORKFLOW_NAME"]
    assert substitutions["_WORKFLOW_SERVICE_ACCOUNT"]


def test_cloudbuild_deploys_repo_workflow_source():
    workflow_step = _cloudbuild_gcloud_step("workflows", "deploy")

    assert workflow_step["args"][2] == "$_WORKFLOW_NAME"
    assert "--source=workflows/pipeline.yaml" in workflow_step["args"]
    assert "--project=$_PROJECT_ID" in workflow_step["args"]
    assert "--location=$_REGION" in workflow_step["args"]
    assert "--service-account=$_WORKFLOW_SERVICE_ACCOUNT" in workflow_step["args"]
    env_map = _parse_arg_pairs(workflow_step["args"], "--set-env-vars=")
    assert env_map["PIPELINE_SERVICE_BASE_URL"] == "$_PIPELINE_SERVICE_BASE_URL"
    assert env_map["PROJECT_ID"] == "$_PROJECT_ID"
    assert env_map["REGION"] == "$_REGION"
    assert env_map["DOC_AI_LOCATION"] == "us"
    assert env_map["DOC_AI_PROCESSOR_ID"] == "21c8becfabc49de6"
    assert env_map["SUMMARISER_JOB_NAME"] == "$_SUMMARISER_JOB_NAME"
    assert env_map["PDF_JOB_NAME"] == "$_PDF_JOB_NAME"
    assert env_map["INTAKE_GCS_BUCKET"] == "mcc-intake"
    assert env_map["OUTPUT_GCS_BUCKET"] == "mcc-output"
    assert env_map["SUMMARY_BUCKET"] == "mcc-output"
    assert env_map["MAX_SHARD_CONCURRENCY"] == "12"
    assert env_map["WORKFLOW_CALLER_SA"] == "$_WORKFLOW_SERVICE_ACCOUNT"


def test_cloudbuild_deploys_current_summary_jobs() -> None:
    summariser_step = _cloudbuild_gcloud_step(
        "run", "jobs", "deploy", "$_SUMMARISER_JOB_NAME"
    )
    pdf_step = _cloudbuild_gcloud_step("run", "jobs", "deploy", "$_PDF_JOB_NAME")

    for step, module_name, task_timeout in (
        (summariser_step, "src.services.summariser_refactored", "10800s"),
        (pdf_step, "src.services.pdf_writer_refactored", "900s"),
    ):
        args = step["args"]
        assert "--image=$_IMAGE_REPO:$_TAG" in args
        assert "--project=$_PROJECT_ID" in args
        assert "--region=$_REGION" in args
        assert "--service-account=$_JOB_SERVICE_ACCOUNT" in args
        assert "--tasks=1" in args
        assert "--max-retries=3" in args
        assert f"--task-timeout={task_timeout}" in args
        assert "--cpu=1" in args
        assert "--memory=2Gi" in args
        assert f"--command=python,-m,{module_name}" in args
        assert "--args=" in args
        assert all(not arg.startswith("--args=-m,") for arg in args)

        env_map = _parse_arg_pairs(args, "--set-env-vars=")
        assert env_map["PROJECT_ID"] == "$_PROJECT_ID"
        assert env_map["REGION"] == "$_REGION"
        assert env_map["PIPELINE_STATE_BACKEND"] == "gcs"
        assert env_map["PIPELINE_STATE_BUCKET"] == "$_PIPELINE_STATE_BUCKET"
        assert env_map["PIPELINE_STATE_PREFIX"] == "pipeline-state"
        assert env_map["SUMMARY_BUCKET"] == "mcc-output"
        assert env_map["OUTPUT_GCS_BUCKET"] == "mcc-output"
        assert env_map["SUMMARY_SCHEMA_VERSION"] == "2025-10-01"
        if step is summariser_step:
            assert env_map["SUMMARY_STRATEGY"] == "auto"
            assert env_map["SUMMARY_CHUNKED_MODEL"] == "gpt-4.1-mini"
            assert env_map["SUMMARY_ONE_SHOT_MODEL"] == "gpt-5.4"
            assert env_map["SUMMARY_ONE_SHOT_REASONING_EFFORT"] == "none"
            assert env_map["SUMMARY_ONE_SHOT_TOKEN_THRESHOLD"] == "120000"
            assert env_map["SUMMARY_ONE_SHOT_MAX_PAGES"] == "80"
            assert env_map["SUMMARY_OCR_NOISE_RATIO_THRESHOLD"] == "0.18"

    summariser_secrets = _parse_arg_pairs(
        summariser_step["args"], "--update-secrets="
    )
    assert summariser_secrets["OPENAI_API_KEY"] == "OPENAI_API_KEY:latest"


def test_workflow_internal_event_callbacks_use_ingest_prefix():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    lines = workflow_text.splitlines()
    event_callback_lines = [
        line
        for line in lines
        if "url:" in line and '"/ingest/internal/jobs/" + jobId + "/events"' in line
    ]

    assert event_callback_lines


def test_workflow_includes_internal_drive_upload_callback():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    assert '"/ingest/internal/jobs/" + jobId + "/upload-report"' in workflow_text


def test_workflow_includes_summary_input_preparation_callback():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    assert '"/ingest/internal/jobs/" + jobId + "/prepare-summary-input"' in workflow_text
    assert "requiresOcr" in workflow_text
    assert "summaryInputUri" in workflow_text


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


def test_workflow_pdf_job_skips_signed_url_generation():
    workflow_doc = yaml.safe_load(pathlib.Path("workflows/pipeline.yaml").read_text())
    all_strings = _collect_strings(workflow_doc)
    assert "--skip-signed-url" in all_strings


def test_workflow_persists_pdf_uri_and_report_file_id():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    assert "status: PDF_DONE" in workflow_text
    assert "pdfUri: ${finalPdfUri}" in workflow_text
    assert "status: UPLOADED" in workflow_text
    assert "metadataPatch:" in workflow_text
    assert "report_file_id: ${driveUploadResult.body.report_file_id}" in workflow_text


def test_workflow_fails_closed_when_async_jobs_fail():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()
    assert "googleapis.run.v2.projects.locations.jobs.executions.get" in workflow_text
    assert '"/ingest/internal/jobs/" + jobId + "/verify-artifact"' in workflow_text
    assert "Summariser execution failed:" in workflow_text
    assert "Summariser execution cancelled:" in workflow_text
    assert "Summariser execution did not succeed:" in workflow_text
    assert "PDF execution failed:" in workflow_text
    assert "PDF execution cancelled:" in workflow_text
    assert "PDF execution did not succeed:" in workflow_text
    assert 'default(map.get(summariserStatus, "failedCount"), 0)' in workflow_text
    assert 'default(map.get(summariserStatus, "succeededCount"), 0)' in workflow_text
    assert 'default(map.get(summariserStatus, "cancelledCount"), 0)' in workflow_text
    assert 'default(map.get(pdfStatus, "failedCount"), 0)' in workflow_text
    assert 'default(map.get(pdfStatus, "succeededCount"), 0)' in workflow_text
    assert 'default(map.get(pdfStatus, "cancelledCount"), 0)' in workflow_text
    assert 'gcsUri: ${"gs://" + summaryBucket + "/summaries/" + jobId + ".json"}' in workflow_text
    assert "gcsUri: ${finalPdfUri}" in workflow_text
    assert 'raise: \'${"Pipeline failed after markFailed: " + text.decode(json.encode(e))}\'' in workflow_text


def test_workflow_verifies_artifacts_before_follow_on_callbacks():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()

    assert workflow_text.index("- verifySummaryArtifact:") < workflow_text.index(
        "- markSupervisorDone:"
    )
    assert workflow_text.index("- verifyPdfArtifact:") < workflow_text.index(
        "- markPdfDone:"
    )
    assert workflow_text.index("- rethrowFailure:") < workflow_text.index("- complete:")


def test_workflow_retries_summary_artifact_before_pdf_stage():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()

    assert "summaryArtifactAttempts" in workflow_text
    assert "Summary artifact missing after summariser success:" in workflow_text
    assert workflow_text.count('"/ingest/internal/jobs/" + jobId + "/verify-artifact"') >= 2
    assert workflow_text.index("- initSummaryArtifactWait:") < workflow_text.index(
        "- markSupervisorDone:"
    )
    assert workflow_text.index("- sleepSummaryArtifact:") < workflow_text.index(
        "- markSupervisorDone:"
    )
    assert workflow_text.index("- retrySummaryArtifactVerification:") < workflow_text.index(
        "- markSupervisorDone:"
    )


def test_workflow_extends_summariser_timeouts_for_large_docs():
    workflow_text = pathlib.Path("workflows/pipeline.yaml").read_text()

    assert 'timeout: "10800s"' in workflow_text
    assert "connector_params:" in workflow_text
    assert "timeout: 21600" in workflow_text

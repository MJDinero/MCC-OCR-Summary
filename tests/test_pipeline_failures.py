import logging

from src.utils.pipeline_failures import publish_pipeline_failure


def test_publish_pipeline_failure_logs_stub_payload(caplog):
    caplog.set_level(logging.WARNING, logger="pipeline_failures")

    publish_pipeline_failure("job-123", stage="ocr", retries=2)

    assert any(record.message == "pipeline_failure_stub" for record in caplog.records)
    stub_record = next(
        record for record in caplog.records if record.message == "pipeline_failure_stub"
    )
    assert getattr(stub_record, "args_len", None) == 1
    assert getattr(stub_record, "kwargs_keys", None) == ["retries", "stage"]

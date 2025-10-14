import pytest
from google.api_core import exceptions as gexc

from src.services.docai_helper import _poll_operation, OCRServiceError


class _FlakyOperation:
    def __init__(self, succeed_after: int):
        self.succeed_after = succeed_after
        self.calls = 0

    def result(self, timeout=None):  # noqa: D401
        self.calls += 1
        if self.calls <= self.succeed_after:
            raise gexc.DeadlineExceeded("slow")
        return {"success": True}


def test_poll_operation_backs_off_and_succeeds():
    op = _FlakyOperation(succeed_after=2)
    sleeps: list[float] = []

    result = _poll_operation(
        op,
        stage="docai_splitter",
        job_id="job-1",
        trace_id="trace-1",
        sleep_fn=lambda seconds: sleeps.append(round(seconds, 2)),
        initial_delay=1.0,
        max_delay=5.0,
        max_attempts=5,
    )

    assert result["success"] is True
    assert op.calls == 3  # two failures + final success
    assert len(sleeps) == 2
    assert sleeps[0] >= 1.0
    assert sleeps[1] >= sleeps[0]


def test_poll_operation_raises_after_exhausting_attempts():
    op = _FlakyOperation(succeed_after=5)
    with pytest.raises(OCRServiceError):
        _poll_operation(
            op,
            stage="docai_ocr",
            job_id="job-2",
            trace_id="trace-2",
            sleep_fn=lambda _seconds: None,
            initial_delay=0.5,
            max_delay=1.0,
            max_attempts=2,
        )

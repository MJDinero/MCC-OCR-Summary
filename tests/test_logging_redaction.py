from __future__ import annotations

import io
import logging
import uuid

from src.utils.logging_filter import PHIRedactFilter
from src.utils.redact import REDACTION_TOKEN


def _build_logger(format_str: str = "%(message)s") -> tuple[logging.Logger, logging.Handler, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(format_str))
    handler.addFilter(PHIRedactFilter())
    logger = logging.getLogger(f"phi-redaction-{uuid.uuid4()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger, handler, stream


def test_phi_filter_redacts_message_and_args() -> None:
    logger, handler, stream = _build_logger()
    logger.info("Patient %s SSN 123-45-6789", "12-3456789")
    handler.flush()
    payload = stream.getvalue()
    assert REDACTION_TOKEN in payload
    assert "123-45-6789" not in payload
    assert "12-3456789" not in payload


def test_phi_filter_redacts_structured_extras() -> None:
    logger, handler, stream = _build_logger("%(detail)s %(context)s")
    logger.info(
        "structured",
        extra={
            "detail": "Contact 555-111-2222",
            "context": {"mrn": "12-3456789", "notes": ["call 555-111-2222"]},
        },
    )
    handler.flush()
    payload = stream.getvalue()
    assert payload.count(REDACTION_TOKEN) >= 2
    assert "555-111-2222" not in payload
    assert "12-3456789" not in payload

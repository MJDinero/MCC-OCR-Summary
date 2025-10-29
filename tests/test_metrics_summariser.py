from src.services.metrics_summariser import (
    reset_state,
    snapshot_state,
    record_chunks,
    record_chunk_chars,
    record_fallback_run,
    record_needs_review,
    record_collapse_run,
)


def test_metrics_helpers_track_state() -> None:
    reset_state()
    record_chunks(3)
    record_chunk_chars(500)
    record_chunk_chars(700)
    record_fallback_run()
    record_needs_review()
    record_collapse_run()

    state = snapshot_state()
    assert state["chunks_total"] == 3
    assert state["fallback_runs"] == 1
    assert state["needs_review"] == 1
    assert state["collapse_runs"] == 1
    assert 500 <= state["avg_chunk_chars"] <= 700

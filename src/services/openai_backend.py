"""Thin OpenAI wrapper supporting OpenAI Responses and Chat APIs."""
from __future__ import annotations

from typing import Any, Optional

from openai import OpenAI


def _extract_responses_text(resp: Any) -> str:
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt
    output = getattr(resp, "output", None) or getattr(resp, "data", None)
    if not output:
        raise RuntimeError("OpenAI Responses: empty payload")
    node = output[0]
    try:
        return node.content[0].text.value  # type: ignore[union-attr]
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise RuntimeError("OpenAI Responses: could not extract text") from exc


def call_llm(
    *,
    prompt: str,
    use_responses: bool,
    model: str,
    json_mode: bool,
    api_key: Optional[str] = None,
) -> str:
    """Call OpenAI via Responses (no response_format) or Chat JSON mode."""
    client = OpenAI(api_key=api_key)
    if use_responses:
        resp = client.responses.create(model=model, input=prompt)
        return _extract_responses_text(resp)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    completion = client.chat.completions.create(**kwargs)
    content = completion.choices[0].message.content
    if content is None:
        raise RuntimeError("OpenAI Chat returned empty content")
    return content

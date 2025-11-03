# AGENTS.md — MCC‑OCR‑Summary

## Purpose
This file provides guidance for AI coding agents (e.g., GPT‑5‑Codex) working on this repository. It outlines environment setup, test commands, and acceptance criteria. Agents should treat this repository as **read‑only** unless explicitly instructed by a user or prompt.

## Setup
- Use Python 3.10 or higher.
- Install dependencies: `python3 -m pip install -r requirements-dev.txt`.
- (Optional) Create a virtual environment: `python3 -m venv .venv && source .venv/bin/activate`.

## Test Commands
- Run the full test suite: `python3 -m pytest --no-cov`.
- To run specific tests: e.g., `python3 -m pytest --no-cov tests/test_format_contract.py`.
- Some tests require environment variables; set `PROJECT_ID=stub` when running locally.

## Acceptance Criteria
- All tests under `tests/` pass.
- The PDF summary includes exactly four canonical narrative sections (Intro Overview, Key Points, Detailed Findings, Care Plan & Follow‑Up) and the three entity lists (Diagnoses, Providers, Medications / Prescriptions).
- No “Structured Indices,” “Summary Lists,” or chunk metadata appear in the final PDF.

## Guidelines
- **Do not modify or commit code** unless explicitly asked. Use prompts to review and audit.
- If you need to verify the pipeline, generate a sample PDF using the local API (see README) and inspect it, but avoid changing any files.
- When running commands that interact with external services (Doc AI, Google Drive, Cloud Run), ensure proper authentication is configured locally and avoid printing sensitive tokens.
- Summarise your findings in a report; do not push changes unless directed.
---
# AGENTS.md — MCC‑OCR‑Summary
# AGENTS.md — MCC‑OCR‑Summary

## Purpose
This file provides guidance for AI coding agents (e.g., GPT‑5‑Codex) working on this repository. It outlines environment setup, test commands, and acceptance criteria. Agents should treat this repository as **read‑only** unless explicitly instructed by a user or prompt.

## Setup
- Use Python 3.10 or higher.
- Install dependencies: `python3 -m pip install -r requirements-dev.txt`.
- (Optional) Create a virtual environment: `python3 -m venv .venv && source .venv/bin/activate`.

## Test Commands
- Run the full test suite: `python3 -m pytest --no-cov`.
- To run specific tests: e.g., `python3 -m pytest --no-cov tests/test_format_contract.py`.
- Some tests require environment variables; set `PROJECT_ID=stub` when running locally.

## Acceptance Criteria
- All tests under `tests/` pass.
- The PDF summary includes exactly four canonical narrative sections (Intro Overview, Key Points, Detailed Findings, Care Plan & Follow‑Up) and the three entity lists (Diagnoses, Providers, Medications / Prescriptions).
- No “Structured Indices,” “Summary Lists,” or chunk metadata appear in the final PDF.

## Guidelines
- **Do not modify or commit code** unless explicitly asked. Use prompts to review and audit.
- If you need to verify the pipeline, generate a sample PDF using the local API (see README) and inspect it, but avoid changing any files.
- When running commands that interact with external services (Doc AI, Google Drive, Cloud Run), ensure proper authentication is configured locally and avoid printing sensitive tokens.
- Summarise your findings in a report; do not push changes unless directed.
## Purpose
…[same content as above]…
---

"""Custom exception hierarchy for MCC-OCR-Summary service.

These errors provide clear, typed failure modes across the microservice so
FastAPI exception handlers can map them to appropriate HTTP status codes and
logs can be structured consistently.
"""
from __future__ import annotations

class ValidationError(Exception):
    """Raised when user supplied input (file upload, parameters) is invalid."""


class OCRServiceError(Exception):
    """Raised when the underlying OCR (Document AI) service fails permanently."""


class SummarizationError(Exception):
    """Raised when summarisation backend fails or returns unusable output."""


class PDFGenerationError(Exception):
    """Raised when PDF rendering fails."""


class DriveServiceError(Exception):
    """Raised when Google Drive interactions fail."""


__all__ = [
    "ValidationError",
    "OCRServiceError",
    "SummarizationError",
    "PDFGenerationError",
    "DriveServiceError",
]

"""Routers for MCC OCR Summary FastAPI application."""

from __future__ import annotations

from fastapi import APIRouter

from .ingest import router as ingest_router
from .process import router as process_router


def build_api_router() -> APIRouter:
    """Combine all API routers for inclusion in the FastAPI app."""
    router = APIRouter()
    router.include_router(process_router)
    router.include_router(ingest_router)
    return router


__all__ = ["build_api_router"]

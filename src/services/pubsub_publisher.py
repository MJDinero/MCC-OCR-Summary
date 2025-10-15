"""Asynchronous Pub/Sub publisher implementation."""

from __future__ import annotations

import asyncio
from typing import Dict

from google.cloud import pubsub_v1  # type: ignore

from .interfaces import PubSubPublisher


class AsyncPubSubPublisher(PubSubPublisher):
    """Async wrapper around the Pub/Sub PublisherClient."""

    def __init__(self, client: pubsub_v1.PublisherClient | None = None) -> None:
        self.client = client or pubsub_v1.PublisherClient()

    async def publish(
        self,
        topic: str,
        data: bytes,
        attributes: Dict[str, str] | None = None,
    ) -> str:
        future = self.client.publish(topic, data, **(attributes or {}))
        return await asyncio.wrap_future(future)


__all__ = ["AsyncPubSubPublisher"]

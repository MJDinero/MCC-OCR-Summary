import pytest

from src.services.chunker import Chunker


def test_chunker_splits_large_text():
    chunker = Chunker(max_tokens=200, min_tokens=100)
    text = "Sentence. " * 300
    chunks = list(chunker.chunk_sync([text]))
    assert len(chunks) > 1
    assert all(chunk.token_count <= 200 for chunk in chunks)
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 1


@pytest.mark.asyncio
async def test_chunker_async_iteration():
    chunker = Chunker(max_tokens=150, min_tokens=80)

    async def pages():
        for _ in range(3):
            yield " ".join(["This is a streaming page." for _ in range(50)])

    chunks = [chunk async for chunk in chunker.chunk_async(pages())]
    assert chunks
    assert chunks[0].index == 0
    assert chunks[-1].index == len(chunks) - 1

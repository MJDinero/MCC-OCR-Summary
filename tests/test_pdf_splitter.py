import json
from pathlib import Path
from unittest.mock import patch

from PyPDF2 import PdfWriter

from src.utils.pdf_splitter import split_pdf_by_page_limit


class InMemoryBlob:
    def __init__(self, name, store):
        self.name = name
        self._store = store

    def upload_from_filename(self, filename):
        self._store[self.name] = Path(filename).read_bytes()

    def upload_from_string(self, data, content_type="text/plain"):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._store[self.name] = data


class InMemoryBucket:
    def __init__(self, name, store):
        self.name = name
        self._store = store

    def blob(self, name):
        return InMemoryBlob(name, self._store)


class InMemoryStorageClient:
    def __init__(self):
        self._store = {}

    def bucket(self, name):
        return InMemoryBucket(name, self._store)

    # Helper for tests to list stored blob names
    def list_names(self):  # pragma: no cover - trivial
        return list(self._store.keys())


def _make_pdf(tmp_path: Path, pages: int) -> Path:
    writer = PdfWriter()
    # Empty pages
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    p = tmp_path / f"sample_{pages}.pdf"
    with p.open("wb") as f:
        writer.write(f)
    return p


def test_no_split_required(tmp_path):
    pdf = _make_pdf(tmp_path, 10)
    client = InMemoryStorageClient()
    with patch("src.utils.pdf_splitter.storage.Client", return_value=client):
        result = split_pdf_by_page_limit(str(pdf), max_pages=200)
    # Single part pointing to uploaded original file copy
    assert len(result.parts) == 1
    # Manifest present in store
    manifest_blob = [k for k in client.list_names() if k.endswith("manifest.json")][0]
    manifest = json.loads(client._store[manifest_blob].decode("utf-8"))
    assert manifest["total_pages"] == 10
    assert manifest["parts"][0]["page_end"] == 10


def test_split_into_multiple_parts(tmp_path):
    pdf = _make_pdf(tmp_path, 425)
    client = InMemoryStorageClient()
    with patch("src.utils.pdf_splitter.storage.Client", return_value=client):
        result = split_pdf_by_page_limit(str(pdf), max_pages=200)
    assert len(result.parts) == 3  # 200 + 200 + 25
    manifest_blob = [k for k in client.list_names() if k.endswith("manifest.json")][0]
    manifest = json.loads(client._store[manifest_blob].decode("utf-8"))
    assert manifest["total_pages"] == 425
    assert manifest["parts"][0]["page_start"] == 1
    assert manifest["parts"][1]["page_start"] == 201
    assert manifest["parts"][-1]["page_end"] == 425
    # Ensure pdf blobs exist
    pdf_part_names = [p["name"] for p in manifest["parts"]]
    for name in pdf_part_names:
        assert any(k.endswith(name) for k in client.list_names())

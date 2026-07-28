"""Unit tests for the format-specific read-only source readers."""

import json
from pathlib import Path

import pytest

from defense_research_agent.data.readers import (
    JsonPublicationReader,
    PdfPublicationReader,
    PublicationReader,
    SkipSourceFile,
    SourceFileKind,
)


def test_readers_share_common_interface_and_parse_observed_formats(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    metadata_dir = input_root / "metadata"
    pdf_dir = input_root / "Brief"
    metadata_dir.mkdir(parents=True)
    pdf_dir.mkdir()

    pdf_path = pdf_dir / "2024_홍길동_한글국방정책.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    json_path = metadata_dir / "2024_홍길동_한글국방정책.json"
    json_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "filename": pdf_path.name,
                    "category": "Brief",
                    "processed_date": "2026-02-02T12:30:00",
                },
                "full_text": "한글 본문을 손상 없이 읽는다.",
                "page_texts": [
                    {
                        "page": 1,
                        "text": "한글 본문을 손상 없이 읽는다.",
                        "char_count": 18,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    readers: tuple[PublicationReader, ...] = (
        JsonPublicationReader(),
        PdfPublicationReader(),
    )
    json_source = readers[0].read(json_path, input_root)
    pdf_source = readers[1].read(pdf_path, input_root)

    assert json_source.kind is SourceFileKind.DOCUMENT_JSON
    assert json_source.content == "한글 본문을 손상 없이 읽는다."
    assert json_source.created_at is not None
    assert pdf_source.kind is SourceFileKind.PDF
    assert len(pdf_source.checksum) == 64


def test_json_reader_skips_observed_aggregate_index(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    input_root.mkdir()
    index_path = input_root / "pdf_index.json"
    index_path.write_text(
        json.dumps({"total_documents": 0, "documents": []}),
        encoding="utf-8",
    )

    with pytest.raises(SkipSourceFile, match="aggregate"):
        JsonPublicationReader().read(index_path, input_root)


def test_pdf_reader_rejects_corrupt_header(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    input_root.mkdir()
    pdf_path = input_root / "broken.pdf"
    pdf_path.write_bytes(b"not a pdf")

    with pytest.raises(ValueError, match="PDF header"):
        PdfPublicationReader().read(pdf_path, input_root)

"""Integration tests for read-only collection, normalization, and reporting."""

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import cast
from unicodedata import normalize

import pytest

from defense_research_agent.data.readers import (
    JsonPublicationReader,
    PdfPublicationReader,
)
from defense_research_agent.domain import JsonObject, PublicationType
from defense_research_agent.services.ingestion import IngestionService
from defense_research_agent.services.publication_type import classify_publication_type

PROJECT_ROOT = Path(__file__).parents[2]
REAL_DATA_ROOT = PROJECT_ROOT / "data"


def _write_pdf(input_root: Path, category: str, filename: str) -> Path:
    category_dir = input_root / category
    category_dir.mkdir(parents=True, exist_ok=True)
    path = category_dir / filename
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return path


def _write_document_json(
    input_root: Path,
    json_name: str,
    pdf_filename: str,
    category: str,
    content: str,
    processed_date: str = "2026-02-02T12:30:00",
) -> Path:
    metadata_dir = input_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    path = metadata_dir / json_name
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "filename": pdf_filename,
                    "path": f"/obsolete/source/{category}/{pdf_filename}",
                    "category": category,
                    "num_pages": 1,
                    "file_size_mb": 0.01,
                    "processed_date": processed_date,
                    "total_chars": len(content),
                    "avg_chars_per_page": len(content),
                },
                "full_text": content,
                "page_texts": [{"page": 1, "text": content, "char_count": len(content)}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(path for path in root.rglob("*") if path.is_file())
    }


def test_mixed_publication_types_preserve_korean_and_source_files(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    cases = [
        ("Brief", "2024_김하나_인공지능과국방혁신.pdf", "브리프 한글 본문"),
        ("국방논단", "2025_이둘_방위산업발전.pdf", "국방논단 한글 본문"),
        ("국방정책연구", "2026_박셋_억제전략연구.pdf", "국방정책연구 한글 본문"),
        ("연구보고서", "2023_최넷_국방인력보고서.pdf", "연구보고서 한글 본문"),
    ]
    for index, (category, filename, content) in enumerate(cases):
        _write_pdf(input_root, category, filename)
        _write_document_json(
            input_root,
            f"record-{index}.json",
            filename,
            category,
            content,
        )
    (input_root / ".DS_Store").write_bytes(b"ignored")
    before_hashes = _file_hashes(input_root)

    output_dir = tmp_path / "artifacts" / "normalized"
    outcome = IngestionService().ingest(input_root, output_dir)

    assert outcome.report.total_file_count == 9
    assert outcome.report.success_count == 8
    assert outcome.report.failure_count == 0
    assert outcome.report.skipped_count == 1
    assert outcome.report.publication_count == 4
    assert outcome.report.publication_type_counts == {
        "defense_forum": 1,
        "defense_policy_research": 1,
        "kida_brief": 1,
        "research_report": 1,
    }
    assert _file_hashes(input_root) == before_hashes

    output_text = (output_dir / "publications.jsonl").read_text(encoding="utf-8")
    assert "브리프 한글 본문" in output_text
    assert "\\ube0c\\ub9ac\\ud504" not in output_text
    assert outcome.report_path == tmp_path / "artifacts" / "reports" / "ingestion_report.json"


def test_unknown_and_corrupt_files_are_reported_without_stopping(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    _write_pdf(input_root, "Brief", "2024_김하나_정상자료.pdf")
    (input_root / "unknown.txt").write_text("unsupported", encoding="utf-8")
    (input_root / "broken.pdf").write_bytes(b"broken")
    (input_root / "broken.json").write_bytes(b"\xff\xfe")

    outcome = IngestionService().ingest(
        input_root,
        tmp_path / "artifacts" / "normalized",
    )

    assert outcome.report.publication_count == 1
    assert outcome.report.success_count == 1
    assert outcome.report.failure_count == 3
    assert {failure.path for failure in outcome.report.failures} == {
        "broken.json",
        "broken.pdf",
        "unknown.txt",
    }
    assert {failure.error_type for failure in outcome.report.failures} >= {
        "UnsupportedFileFormat",
        "ValueError",
        "UnicodeDecodeError",
    }


def test_duplicate_records_merge_and_ids_are_deterministic(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    filename = "2025_김민관_북한의대남심리전변화분석.pdf"
    _write_pdf(input_root, "국방정책연구", filename)
    for index in range(3):
        _write_document_json(
            input_root,
            f"record {index + 1}.json",
            filename,
            "국방정책연구",
            "중복이어도 하나의 한글 본문으로 정규화한다.",
            processed_date=f"2026-02-02T12:30:0{index}",
        )

    first = IngestionService().ingest(
        input_root,
        tmp_path / "artifacts-one" / "normalized",
    )
    second = IngestionService().ingest(
        input_root,
        tmp_path / "artifacts-two" / "normalized",
    )

    assert first.report.publication_count == 1
    assert first.report.suspected_duplicate_count == 2
    assert first.report.suspected_duplicate_group_count == 1
    assert first.publications[0].publication_id == second.publications[0].publication_id
    assert first.publications[0].checksum == second.publications[0].checksum
    ingestion_metadata = cast(JsonObject, first.publications[0].raw_metadata["_ingestion"])
    assert len(cast(list[object], ingestion_metadata["json_source_paths"])) == 3


def test_cli_writes_default_artifact_paths(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    _write_pdf(input_root, "국방논단", "2024_홍길동_기후안보정책.pdf")
    output_dir = tmp_path / "artifacts" / "normalized"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent.cli.ingest",
            "--input",
            str(input_root),
            "--output",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert (output_dir / "publications.jsonl").is_file()
    assert (tmp_path / "artifacts" / "reports" / "ingestion_report.json").is_file()
    assert cast(dict[str, object], json.loads(completed.stdout))["publication_count"] == 1


def test_output_is_rejected_inside_read_only_input(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    input_root.mkdir()

    with pytest.raises(ValueError, match="outside"):
        IngestionService().ingest(input_root, input_root / "generated")


def _find_real_category_dir(category: str) -> Path:
    normalized_category = normalize("NFC", category)
    return next(
        path
        for path in REAL_DATA_ROOT.iterdir()
        if path.is_dir() and normalize("NFC", path.name) == normalized_category
    )


def _find_real_pdf(category: str, filename: str) -> Path:
    normalized_filename = normalize("NFC", filename)
    return next(
        path
        for path in _find_real_category_dir(category).iterdir()
        if path.is_file() and normalize("NFC", path.name) == normalized_filename
    )


def test_actual_representative_records_parse_for_primary_categories() -> None:
    wanted = {"Brief", "국방논단", "국방정책연구"}
    selected: dict[str, tuple[Path, dict[str, object]]] = {}
    for path in sorted((REAL_DATA_ROOT / "metadata").glob("*.json")):
        payload = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            continue
        category = metadata.get("category")
        if isinstance(category, str) and category in wanted and category not in selected:
            selected[category] = (path, payload)
        if selected.keys() == wanted:
            break

    assert selected.keys() == wanted
    json_reader = JsonPublicationReader()
    pdf_reader = PdfPublicationReader()
    observed_types: set[PublicationType] = set()
    for category, (json_path, payload) in selected.items():
        metadata = cast(dict[str, object], payload["metadata"])
        filename = cast(str, metadata["filename"])
        pdf_path = _find_real_pdf(category, filename)
        json_source = json_reader.read(json_path, REAL_DATA_ROOT)
        pdf_source = pdf_reader.read(pdf_path, REAL_DATA_ROOT)

        observed_types.add(
            classify_publication_type(
                json_source.source_path,
                json_source.raw_metadata,
                json_source.content,
            )
        )
        assert json_source.content
        assert normalize("NFC", json_source.target_filename) == normalize(
            "NFC", pdf_source.target_filename
        )
        assert len(pdf_source.checksum) == 64

    assert observed_types == {
        PublicationType.KIDA_BRIEF,
        PublicationType.DEFENSE_FORUM,
        PublicationType.DEFENSE_POLICY_RESEARCH,
    }

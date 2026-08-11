"""Unit tests for the deterministic corpus chunk build entry point."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Sequence
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from defense_research_agent.domain import (
    ExtractionProvenance,
    IngestionReport,
    JsonObject,
    PublicationPage,
    PublicationQualityStatus,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.evaluation.quality import DeterministicPublicationQualityGate
from defense_research_agent.services.ingestion import IngestionOutcome

if TYPE_CHECKING:
    import scripts.build_corpus_chunks as build
else:
    script_path = Path(__file__).parents[2] / "scripts" / "build_corpus_chunks.py"
    spec = spec_from_file_location("scripts.build_corpus_chunks", script_path)
    if spec is None or spec.loader is None:  # pragma: no cover - fixed repository layout
        raise RuntimeError(f"could not load {script_path}")
    build = module_from_spec(spec)
    sys.modules[spec.name] = build
    spec.loader.exec_module(build)

AUDIT_STATUS_COUNTS = {
    PublicationQualityStatus.READY: 269,
    PublicationQualityStatus.WARNING: 28,
    PublicationQualityStatus.LOW_TEXT: 38,
    PublicationQualityStatus.CORRUPT_TEXT: 1,
    PublicationQualityStatus.DUPLICATE: 0,
    PublicationQualityStatus.ORPHAN_PDF: 1,
    PublicationQualityStatus.MANUAL_REVIEW: 34,
}


def _publication(
    publication_id: str,
    *,
    local_path: str = "data/연구보고서/2025_저자_정상제목.pdf",
    title_source: str = "cover",
    source_filename: str | None = None,
    selected_source_path: str = "metadata/document.json",
    selected_source_checksum: str = "f" * 64,
) -> ResearchPublication:
    raw_metadata: JsonObject = {
        "filename": source_filename or Path(local_path).name,
        "_ingestion": {
            "selected_source_path": selected_source_path,
            "json_source_paths": [selected_source_path],
            "json_source_checksums": [selected_source_checksum],
            "pdf_linked": True,
            "title_source": title_source,
        },
    }
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=PublicationType.RESEARCH_REPORT,
        title="국방정책 연구",
        local_path=local_path,
        raw_metadata=raw_metadata,
    )


def _page(text: str, *, source_checksum: str = "f" * 64) -> PublicationPage:
    return PublicationPage(
        page_number=1,
        text=text,
        provenance=ExtractionProvenance(
            parser_name="fixture-parser",
            parser_version="1.0.0",
            source_checksum=source_checksum,
        ),
    )


def _outcome(
    publications: Sequence[ResearchPublication],
    artifact_root: Path,
) -> IngestionOutcome:
    report_path = artifact_root / "ingestion_report.json"
    return IngestionOutcome(
        publications=tuple(publications),
        report=IngestionReport(
            input_path="data",
            publications_path="artifacts/corpus/normalized/publications.jsonl",
            total_file_count=len(publications),
            success_count=len(publications),
            failure_count=0,
            skipped_count=0,
            publication_count=len(publications),
            suspected_duplicate_count=0,
            suspected_duplicate_group_count=0,
        ),
        report_path=report_path,
    )


def _write_document_json(
    path: Path,
    *,
    filename: str,
    pages: Sequence[tuple[int, object]],
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = json.dumps(
        {
            "metadata": {
                "filename": filename,
                "category": "연구보고서",
            },
            "full_text": "".join(text for _, text in pages if isinstance(text, str)),
            "page_texts": [{"page": number, "text": text} for number, text in pages],
        },
        ensure_ascii=False,
    ).encode()
    path.write_bytes(source_bytes)
    return sha256(source_bytes).hexdigest()


def test_source_tree_digest_tracks_file_content_and_relative_paths_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_directory = tmp_path / "data"
    first_path = data_directory / "first.txt"
    nested_path = data_directory / "nested" / "second.txt"
    nested_path.parent.mkdir(parents=True)
    first_path.write_text("first", encoding="utf-8")
    nested_path.write_text("second", encoding="utf-8")
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)

    initial_digest = build.source_tree_digest()

    assert build.source_tree_digest() == initial_digest

    first_path.write_text("changed", encoding="utf-8")
    content_changed_digest = build.source_tree_digest()
    assert content_changed_digest != initial_digest

    renamed_path = first_path.with_name("renamed.txt")
    first_path.rename(renamed_path)
    path_changed_digest = build.source_tree_digest()
    assert path_changed_digest != content_changed_digest

    (data_directory / "empty-directory").mkdir()
    assert build.source_tree_digest() == path_changed_digest


def test_main_excludes_manual_review_and_records_its_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_directory = tmp_path / "data"
    manual_filename = f"2025_저자_{'가' * 40}.pdf"
    ready_filename = "2025_저자_정상제목.pdf"
    _write_document_json(
        data_directory / "metadata" / "manual.json",
        filename=manual_filename,
        pages=[(1, "가" * 1_200)],
    )
    _write_document_json(
        data_directory / "metadata" / "ready.json",
        filename=ready_filename,
        pages=[(1, "나" * 1_200), (2, "   ")],
    )
    output_directory = tmp_path / "artifacts" / "corpus"

    monkeypatch.setattr(build, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)
    monkeypatch.setattr(build, "OUTPUT_DIRECTORY", output_directory)

    build.main()

    normalized_publications = [
        ResearchPublication.model_validate_json(line)
        for line in (output_directory / "normalized" / "publications.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    publication_ids_by_filename = {
        publication.raw_metadata["filename"]: publication.publication_id
        for publication in normalized_publications
    }
    manual_id = publication_ids_by_filename[manual_filename]
    ready_id = publication_ids_by_filename[ready_filename]
    chunks = (output_directory / "chunks.jsonl").read_text(encoding="utf-8")
    assert f'"publication_id":"{ready_id}"' in chunks
    assert manual_id not in chunks

    failure_report = cast(
        dict[str, object],
        json.loads((output_directory / "quality" / "failure_report.json").read_text()),
    )
    assert failure_report["indexable_publications"] == 1
    assert failure_report["excluded_publications"] == 1
    findings = cast(list[dict[str, object]], failure_report["findings"])
    manual_finding = next(finding for finding in findings if finding["publication_id"] == manual_id)
    assert manual_finding["status"] == "manual_review"
    assert manual_finding["reasons"] == ["파일명 240바이트 이상이며 표지 제목 없음"]

    summary = cast(dict[str, object], json.loads(capsys.readouterr().out))
    results = cast(dict[str, object], summary["results"])
    assert results["selected_document_count"] == 1
    assert results["excluded_document_count"] == 1
    assert results["selected_parser_page_count"] == 1
    assert results["selected_dropped_empty_page_count"] == 1


def test_selection_count_matches_the_371_document_audit_fixture() -> None:
    documents: list[build.ParsedDocument] = []

    for index in range(269):
        body = f"국방정책 연구 본문 {index} " * 100
        documents.append(build.ParsedDocument(_publication(f"ready-{index}"), (_page(body),), 0))
    for index in range(28):
        body = f"국방정책 경고 본문 {index} " * 100 + "\x07"
        documents.append(build.ParsedDocument(_publication(f"warning-{index}"), (_page(body),), 0))
    for index in range(36):
        documents.append(
            build.ParsedDocument(
                _publication(f"low-text-{index}"),
                (_page(f"저추출 {index}"),),
                0,
            )
        )
    low_text_copy = _page("동일 저추출")
    documents.extend(
        [
            build.ParsedDocument(_publication("low-text-copy-1"), (low_text_copy,), 0),
            build.ParsedDocument(_publication("low-text-copy-2"), (low_text_copy,), 0),
        ]
    )
    documents.append(
        build.ParsedDocument(
            _publication("corrupt-text"),
            (_page(("국방정책" + "\x07") * 250),),
            0,
        )
    )
    orphan_metadata: JsonObject = {
        "_ingestion": {
            "pdf_linked": True,
            "json_source_paths": [],
            "json_source_checksums": [],
        }
    }
    orphan = ResearchPublication(
        publication_id="orphan",
        publication_type=PublicationType.RESEARCH_REPORT,
        local_path="data/연구보고서/orphan.pdf",
        raw_metadata=orphan_metadata,
    )
    documents.append(build.ParsedDocument(orphan, (), 0))
    for index in range(34):
        publication = _publication(
            f"manual-review-{index}",
            title_source="filename",
            source_filename=f"2025_저자_{'가' * 40}_{index}.pdf",
        )
        body = f"국방정책 수동검토 본문 {index} " * 100
        documents.append(build.ParsedDocument(publication, (_page(body),), 0))

    _, verdicts = build.evaluate_quality(documents)

    selected = build.select_indexable_documents(documents, verdicts)

    assert len(documents) == 371
    actual_status_counts = Counter(verdict.status for verdict in verdicts.values())
    assert {
        status: actual_status_counts[status] for status in PublicationQualityStatus
    } == AUDIT_STATUS_COUNTS
    assert len(selected) == 297
    assert Counter(
        verdicts[document.publication.publication_id].status for document in selected
    ) == {
        PublicationQualityStatus.READY: 269,
        PublicationQualityStatus.WARNING: 28,
    }


def test_load_parsed_documents_constructs_pages_for_a_valid_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_directory = tmp_path / "data"
    source_path = data_directory / "metadata" / "document.json"
    source_checksum = _write_document_json(
        source_path,
        filename="2025_저자_정상제목.pdf",
        pages=[(1, "첫 페이지"), (2, "둘째 페이지")],
    )
    publication = _publication("parsed", selected_source_checksum=source_checksum)
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)

    documents = build.load_parsed_documents([publication])

    assert len(documents) == 1
    assert documents[0].publication is publication
    assert [page.page_number for page in documents[0].pages] == [1, 2]
    assert [page.text for page in documents[0].pages] == ["첫 페이지", "둘째 페이지"]
    assert documents[0].dropped_empty_page_count == 0


def test_load_parsed_documents_counts_dropped_empty_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_directory = tmp_path / "data"
    source_path = data_directory / "metadata" / "document.json"
    source_checksum = _write_document_json(
        source_path,
        filename="2025_저자_빈페이지포함.pdf",
        pages=[(1, "첫 페이지"), (2, " \n\t"), (3, "셋째 페이지")],
    )
    publication = _publication("empty-page", selected_source_checksum=source_checksum)
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)

    documents = build.load_parsed_documents([publication])

    assert [page.page_number for page in documents[0].pages] == [1, 3]
    assert documents[0].dropped_empty_page_count == 1


def test_load_parsed_documents_fails_closed_for_mixed_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_directory = tmp_path / "data"
    valid_checksum = _write_document_json(
        data_directory / "metadata" / "valid.json",
        filename="2025_저자_정상제목.pdf",
        pages=[(1, "정상 페이지")],
    )
    invalid_checksum = _write_document_json(
        data_directory / "metadata" / "invalid.json",
        filename="2025_저자_손상문서.pdf",
        pages=[(1, 123)],
    )
    valid_publication = _publication(
        "parser-success",
        selected_source_path="metadata/valid.json",
        selected_source_checksum=valid_checksum,
    )
    invalid_publication = _publication(
        "parser-failure",
        selected_source_path="metadata/invalid.json",
        selected_source_checksum=invalid_checksum,
    )
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)

    with pytest.raises(
        RuntimeError,
        match=r"unexpected parser failure for invalid.json: corrupt_structure@1",
    ):
        build.load_parsed_documents([valid_publication, invalid_publication])


def test_duplicate_owner_is_registered_only_after_an_indexable_verdict() -> None:
    body = "국방정책 연구 본문 " * 100
    manual = _publication(
        "manual-first",
        local_path=f"data/Brief/2025_저자_{'가' * 80}.pdf",
        title_source="filename",
    )
    admitted = _publication("admitted-owner")
    duplicate = _publication("duplicate-last")
    documents = [
        build.ParsedDocument(manual, (_page(body),), 0),
        build.ParsedDocument(admitted, (_page(body),), 0),
        build.ParsedDocument(duplicate, (_page(body),), 0),
    ]

    _, verdicts = build.evaluate_quality(documents)

    assert verdicts["manual-first"].status is PublicationQualityStatus.MANUAL_REVIEW
    assert verdicts["admitted-owner"].status is PublicationQualityStatus.READY
    assert verdicts["duplicate-last"].status is PublicationQualityStatus.DUPLICATE
    assert verdicts["duplicate-last"].duplicate_of == "admitted-owner"


def test_non_indexable_duplicates_keep_their_earlier_low_text_status() -> None:
    """Document that low-text precedence prevents either copy claiming ownership."""
    body = "동일 저추출"
    documents = [
        build.ParsedDocument(_publication("first"), (_page(body),), 0),
        build.ParsedDocument(_publication("second"), (_page(body),), 0),
    ]

    _, verdicts = build.evaluate_quality(documents)

    assert verdicts["first"].status is PublicationQualityStatus.LOW_TEXT
    assert verdicts["second"].status is PublicationQualityStatus.LOW_TEXT
    assert verdicts["first"].duplicate_of is None
    assert verdicts["second"].duplicate_of is None


def test_ingestion_order_deterministically_selects_the_duplicate_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creation order cannot change the owner chosen from sorted ingestion input."""
    data_directory = tmp_path / "data"
    body = "국방정책 연구 본문 " * 100
    _write_document_json(
        data_directory / "metadata" / "z-last.json",
        filename="2025_저자_나중문서.pdf",
        pages=[(1, body)],
    )
    _write_document_json(
        data_directory / "metadata" / "a-first.json",
        filename="2025_저자_먼저문서.pdf",
        pages=[(1, body)],
    )
    output_directory = tmp_path / "artifacts" / "corpus"
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)
    monkeypatch.setattr(build, "OUTPUT_DIRECTORY", output_directory)

    ingestion = build.ingest_publications()
    documents = build.load_parsed_documents(ingestion.publications)
    _, verdicts = build.evaluate_quality(documents)
    _, replayed_verdicts = build.evaluate_quality(documents)

    assert [document.publication.local_path for document in documents] == [
        "data/metadata/a-first.json",
        "data/metadata/z-last.json",
    ]
    owner_id = documents[0].publication.publication_id
    duplicate_id = documents[1].publication.publication_id
    assert verdicts[owner_id].status is PublicationQualityStatus.READY
    assert verdicts[duplicate_id].status is PublicationQualityStatus.DUPLICATE
    assert verdicts[duplicate_id].duplicate_of == owner_id
    assert replayed_verdicts == verdicts


def test_json_filename_changes_dq04_decision_when_filesystem_name_is_safe() -> None:
    publication = _publication(
        "json-filename-risk",
        local_path="data/Brief/2025_저자_파일시스템축약명.pdf",
        title_source="filename",
        source_filename=f"2025_저자_{'가' * 40}.pdf",
    )
    pages = (_page("국방정책 연구 본문 " * 100),)

    filesystem_verdict = DeterministicPublicationQualityGate().evaluate(
        publication,
        pages,
        {},
    )
    _, verdicts = build.evaluate_quality([build.ParsedDocument(publication, pages, 0)])

    assert filesystem_verdict.status is PublicationQualityStatus.READY
    assert verdicts[publication.publication_id].status is PublicationQualityStatus.MANUAL_REVIEW
    assert verdicts[publication.publication_id].reasons == [
        "파일명 240바이트 이상이며 표지 제목 없음"
    ]


def test_main_fails_when_the_data_tree_digest_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    (data_directory / "original.txt").write_text("original", encoding="utf-8")
    output_directory = tmp_path / "artifacts" / "corpus"
    outcome = _outcome([], output_directory)

    def mutating_ingestion() -> IngestionOutcome:
        (data_directory / "changed.txt").write_text("changed", encoding="utf-8")
        return outcome

    monkeypatch.setattr(build, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)
    monkeypatch.setattr(build, "OUTPUT_DIRECTORY", output_directory)
    monkeypatch.setattr(build, "ingest_publications", mutating_ingestion)

    with pytest.raises(SystemExit, match="data/ changed during the run"):
        build.main()

    assert (output_directory / "chunks.jsonl").is_file()
    assert (output_directory / "chunks.manifest.json").is_file()


def test_ingest_publications_uses_canonical_identity_and_filename_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_directory = tmp_path / "data"
    source_path = data_directory / "metadata" / "document.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "filename": "2025_저자_국방정책연구.pdf",
                    "category": "연구보고서",
                },
                "full_text": "국방정책 연구 본문",
                "page_texts": [{"page": 1, "text": "국방정책 연구 본문"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_checksum = sha256(source_path.read_bytes()).hexdigest()
    output_directory = tmp_path / "artifacts" / "corpus"
    monkeypatch.setattr(build, "DATA_DIRECTORY", data_directory)
    monkeypatch.setattr(build, "OUTPUT_DIRECTORY", output_directory)

    outcome = build.ingest_publications()

    publication = outcome.publications[0]
    identity = f"publication:v1\0{publication.publication_type.value}\0{source_checksum}".encode()
    assert publication.publication_id == f"pub:kida:{sha256(identity).hexdigest()[:32]}"
    ingestion = cast(dict[str, object], publication.raw_metadata["_ingestion"])
    assert ingestion["title_source"] == "filename"
    assert ingestion["selected_source_path"] == "metadata/document.json"

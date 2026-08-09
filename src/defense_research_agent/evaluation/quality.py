"""Deterministic corpus admission quality measurement and judgement.

Measurement and judgement are separate operations on purpose. Measurements are
observations over extracted page text; judgement applies a versioned threshold
set and can therefore be replayed without parsing the source corpus again.

The original page text is never normalized or rewritten. The U+0001
substitution calibrated in ADR-010 exists only in the local strings used by
:meth:`DeterministicPublicationQualityGate.measure`.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from unicodedata import normalize

from defense_research_agent.domain.common import Checksum, EntityId
from defense_research_agent.domain.publication import (
    PublicationPage,
    ResearchPublication,
)
from defense_research_agent.domain.quality import (
    CONTROL_CHARACTER_SUBSTITUTIONS,
    DEFAULT_QUALITY_THRESHOLDS_VERSION,
    PublicationQualityStatus,
    PublicationQualityVerdict,
    QualityMeasurements,
    QualityThresholds,
)

QUALITY_ARTIFACT_SCHEMA_VERSION = "quality-artifacts-v1"
"""Schema version embedded in every remediation queue and failure report."""

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_QUALITY_ARTIFACT_DIRECTORY = _REPOSITORY_ROOT / "artifacts" / "quality"
REEXTRACT_OCR_QUEUE_FILENAME = "reextract_ocr_queue.jsonl"
FAILURE_REPORT_FILENAME = "failure_report.json"

_ALLOWED_LAYOUT_CONTROLS = frozenset({"\n", "\t", "\r"})
_REMEDIATION_ACTIONS: Mapping[PublicationQualityStatus, tuple[str, ...]] = {
    PublicationQualityStatus.LOW_TEXT: ("reextract", "ocr"),
    PublicationQualityStatus.CORRUPT_TEXT: ("reextract", "ocr"),
    PublicationQualityStatus.ORPHAN_PDF: ("extract_metadata", "extract_text", "ocr"),
}


class PublicationQualityGate(ABC):
    """Decides which publications may enter the default index."""

    @property
    @abstractmethod
    def thresholds(self) -> QualityThresholds:
        """Versioned thresholds this gate judges against."""

    @abstractmethod
    def measure(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> QualityMeasurements:
        """Compute deterministic text measurements without applying thresholds."""

    @abstractmethod
    def evaluate(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        known_content_checksums: Mapping[Checksum, EntityId],
    ) -> PublicationQualityVerdict:
        """Judge one publication against the current thresholds.

        ``known_content_checksums`` maps already-admitted original-text checksums
        to their owning publication. A repeated body is returned as ``duplicate``
        with that owner in ``duplicate_of``.
        """


class DeterministicPublicationQualityGate(PublicationQualityGate):
    """Production quality gate calibrated by ADR-010.

    Status precedence is deliberate: a PDF with no linked document JSON is
    ``orphan_pdf``; duplicates are then isolated before content gates; low
    extraction and corruption take precedence over metadata review; low Korean
    ratio or an unresolved DQ-04 filename-truncation signal becomes
    ``manual_review``; usable text with sparse controls or too many empty pages
    is ``warning``; otherwise it is ``ready``.
    """

    def __init__(self, thresholds: QualityThresholds | None = None) -> None:
        self._thresholds = thresholds or QualityThresholds(
            thresholds_version=DEFAULT_QUALITY_THRESHOLDS_VERSION
        )

    @property
    def thresholds(self) -> QualityThresholds:
        """Return the immutable-by-convention threshold snapshot for this gate."""
        return self._thresholds

    def measure(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> QualityMeasurements:
        """Measure original pages after applying measurement-only substitutions."""
        del publication
        measured_pages = tuple(_substitute_for_measurement(page.text) for page in pages)
        text = "".join(measured_pages)
        control_count = sum(1 for character in text if _is_control(character))
        korean_count = sum(1 for character in text if _is_korean(character))
        unprintable_count = sum(
            1
            for character in text
            if not character.isprintable() and character not in _ALLOWED_LAYOUT_CONTROLS
        )
        character_count = len(text)

        return QualityMeasurements(
            character_count=character_count,
            page_count=len(pages),
            non_empty_page_count=sum(1 for page_text in measured_pages if page_text.strip()),
            control_character_count=control_count,
            printable_ratio=(
                (character_count - unprintable_count) / character_count if character_count else 0.0
            ),
            korean_ratio=korean_count / character_count if character_count else 0.0,
        )

    def evaluate(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage] | None,
        known_content_checksums: Mapping[Checksum, EntityId],
        *,
        measurements: QualityMeasurements | None = None,
        content_checksum: Checksum | None = None,
    ) -> PublicationQualityVerdict:
        """Judge pages or replay a judgement from stored measurements.

        Pass ``pages=None`` and a stored ``measurements`` instance to avoid
        parsing and measuring again. If duplicate detection is in scope for that
        replay, also pass the checksum of the unmodified concatenated page text.
        When pages are supplied the checksum is computed and any supplied value
        is verified, preventing a caller from pairing measurements with a
        different body accidentally.
        """
        if pages is None and measurements is None:
            raise ValueError("pages or stored measurements must be supplied")

        if measurements is None:
            if pages is None:  # pragma: no cover - narrowed by the guard above
                raise AssertionError("unreachable")
            resolved_measurements = self.measure(publication, pages)
        else:
            resolved_measurements = measurements
            if pages is not None and resolved_measurements.page_count != len(pages):
                raise ValueError("stored measurements page_count does not match supplied pages")

        resolved_checksum = content_checksum
        first_page_number: int | None = None
        if pages is not None:
            calculated_checksum = _content_checksum(pages)
            if content_checksum is not None and content_checksum != calculated_checksum:
                raise ValueError("content_checksum does not match supplied page text")
            resolved_checksum = calculated_checksum
            if pages:
                first_page_number = min(page.page_number for page in pages)

        return self.evaluate_measurements(
            publication,
            resolved_measurements,
            known_content_checksums,
            content_checksum=resolved_checksum,
            first_page_number=first_page_number,
        )

    def evaluate_measurements(
        self,
        publication: ResearchPublication,
        measurements: QualityMeasurements,
        known_content_checksums: Mapping[Checksum, EntityId],
        *,
        content_checksum: Checksum | None = None,
        first_page_number: int | None = None,
    ) -> PublicationQualityVerdict:
        """Apply this gate's thresholds to a stored measurement snapshot."""
        limits = self.thresholds

        def verdict(
            status: PublicationQualityStatus,
            *reasons: str,
            duplicate_of: EntityId | None = None,
            manual_review_page: int | None = None,
        ) -> PublicationQualityVerdict:
            return PublicationQualityVerdict(
                publication_id=publication.publication_id,
                status=status,
                measurements=measurements,
                thresholds_version=limits.thresholds_version,
                reasons=list(reasons),
                duplicate_of=duplicate_of,
                manual_review_page=manual_review_page,
            )

        if _is_orphan_pdf(publication):
            return verdict(PublicationQualityStatus.ORPHAN_PDF, "연결된 문서 JSON 없음")

        if known_content_checksums and content_checksum is None:
            raise ValueError(
                "content_checksum is required to replay duplicate detection from measurements"
            )
        owner = (
            known_content_checksums.get(content_checksum) if content_checksum is not None else None
        )
        if owner is not None and owner != publication.publication_id:
            return verdict(
                PublicationQualityStatus.DUPLICATE,
                "동일 본문 checksum",
                duplicate_of=owner,
            )

        if measurements.character_count < limits.min_character_count:
            return verdict(PublicationQualityStatus.LOW_TEXT, "추출 문자 수 미달")
        if measurements.control_character_ratio > limits.max_control_character_ratio:
            return verdict(
                PublicationQualityStatus.CORRUPT_TEXT,
                f"제어문자 비율 {measurements.control_character_ratio:.3f}",
            )
        if measurements.printable_ratio < limits.min_printable_ratio:
            return verdict(PublicationQualityStatus.CORRUPT_TEXT, "출력 가능 문자 비율 미달")
        if measurements.korean_ratio < limits.min_korean_ratio:
            return verdict(PublicationQualityStatus.MANUAL_REVIEW, "한글 비율 미달")

        filename_risk = _filename_title_review_reason(publication)
        if filename_risk is not None:
            return verdict(
                PublicationQualityStatus.MANUAL_REVIEW,
                filename_risk,
                manual_review_page=first_page_number or 1,
            )

        if measurements.non_empty_page_ratio < limits.min_non_empty_page_ratio:
            return verdict(PublicationQualityStatus.WARNING, "빈 페이지 비율 높음")
        if measurements.control_character_count > 0:
            return verdict(
                PublicationQualityStatus.WARNING,
                f"제어문자 {measurements.control_character_count}개",
            )
        return verdict(PublicationQualityStatus.READY)


def select_default_index_publications(
    publications: Sequence[ResearchPublication],
    verdicts: Mapping[EntityId, PublicationQualityVerdict],
) -> list[ResearchPublication]:
    """Fail closed and retain only ``ready``/``warning`` publications for indexing.

    Missing or mismatched verdicts raise instead of silently admitting an
    unmeasured publication. This function is the hand-off boundary for a future
    ingestion/index builder; the search and service packages intentionally do
    not import the evaluation layer directly.
    """
    selected: list[ResearchPublication] = []
    for publication in publications:
        verdict = verdicts.get(publication.publication_id)
        if verdict is None:
            raise ValueError(
                f"missing quality verdict for publication {publication.publication_id}"
            )
        if verdict.publication_id != publication.publication_id:
            raise ValueError(
                f"quality verdict publication mismatch for {publication.publication_id}"
            )
        if verdict.status.is_indexable:
            selected.append(publication)
    return selected


@dataclass(frozen=True, slots=True)
class QualityArtifactPaths:
    """Paths written by :class:`PublicationQualityArtifactWriter`."""

    reextract_ocr_queue: Path
    failure_report: Path


class PublicationQualityArtifactWriter:
    """Write deterministic versioned quality artifacts outside ``data/``."""

    def __init__(
        self,
        output_directory: Path = DEFAULT_QUALITY_ARTIFACT_DIRECTORY,
        *,
        schema_version: str = QUALITY_ARTIFACT_SCHEMA_VERSION,
    ) -> None:
        normalized_version = schema_version.strip()
        if not normalized_version:
            raise ValueError("schema_version must not be blank")
        self._output_directory = _validate_artifact_directory(output_directory)
        self._schema_version = normalized_version

    def write(
        self,
        verdicts: Sequence[PublicationQualityVerdict],
        thresholds: QualityThresholds,
    ) -> QualityArtifactPaths:
        """Atomically write the remediation JSONL queue and JSON failure report."""
        ordered = sorted(verdicts, key=lambda verdict: verdict.publication_id)
        publication_ids = [verdict.publication_id for verdict in ordered]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("quality artifacts require unique publication_id values")
        for verdict in ordered:
            if verdict.thresholds_version != thresholds.thresholds_version:
                raise ValueError(
                    "verdict thresholds_version does not match the report threshold snapshot"
                )

        queue_records = [
            _queue_record(verdict, self._schema_version)
            for verdict in ordered
            if verdict.status in _REMEDIATION_ACTIONS
        ]
        queue_content = "".join(f"{_deterministic_json(record)}\n" for record in queue_records)

        status_counts = {
            status.value: sum(1 for verdict in ordered if verdict.status is status)
            for status in PublicationQualityStatus
        }
        findings = [
            _verdict_record(verdict)
            for verdict in ordered
            if verdict.status is not PublicationQualityStatus.READY
        ]
        failure_report: dict[str, object] = {
            "schema_version": self._schema_version,
            "thresholds": thresholds.model_dump(mode="json"),
            "total_publications": len(ordered),
            "indexable_publications": sum(1 for verdict in ordered if verdict.status.is_indexable),
            "excluded_publications": sum(
                1 for verdict in ordered if not verdict.status.is_indexable
            ),
            "queue_entries": len(queue_records),
            "status_counts": status_counts,
            "findings": findings,
        }
        report_content = f"{_deterministic_json(failure_report, indent=2)}\n"

        self._output_directory.mkdir(parents=True, exist_ok=True)
        queue_path = self._output_directory / REEXTRACT_OCR_QUEUE_FILENAME
        report_path = self._output_directory / FAILURE_REPORT_FILENAME
        _atomic_write_text(queue_path, queue_content)
        _atomic_write_text(report_path, report_content)
        return QualityArtifactPaths(
            reextract_ocr_queue=queue_path,
            failure_report=report_path,
        )


def _substitute_for_measurement(text: str) -> str:
    substituted = text
    for source, replacement in CONTROL_CHARACTER_SUBSTITUTIONS.items():
        substituted = substituted.replace(source, replacement)
    return substituted


def _is_control(character: str) -> bool:
    if character in _ALLOWED_LAYOUT_CONTROLS:
        return False
    codepoint = ord(character)
    return codepoint < 0x20 or 0x7F <= codepoint <= 0x9F


def _is_korean(character: str) -> bool:
    return "가" <= character <= "힣"


def _content_checksum(pages: Sequence[PublicationPage]) -> Checksum:
    body = "".join(page.text for page in pages)
    return sha256(body.encode("utf-8")).hexdigest()


def _filename_title_review_reason(publication: ResearchPublication) -> str | None:
    """Return a DQ-04 review reason only while no cover-derived title exists."""
    if _has_non_filename_title(publication) or publication.local_path is None:
        return None

    filename = publication.local_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    stem = filename.rsplit(".", maxsplit=1)[0]
    normalized_stem = normalize("NFC", stem)
    if normalized_stem and _is_incomplete_hangul_jamo(normalized_stem[-1]):
        return "파일명이 불완전 한글 자모로 끝나며 표지 제목 없음"
    if len(filename.encode("utf-8")) >= 240:
        return "파일명 240바이트 이상이며 표지 제목 없음"
    return None


def _is_incomplete_hangul_jamo(character: str) -> bool:
    """Detect a trailing standalone jamo after complete syllables compose to NFC."""
    codepoint = ord(character)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _is_orphan_pdf(publication: ResearchPublication) -> bool:
    """Return whether ingestion observed a PDF without any linked document JSON."""
    ingestion = _ingestion_lineage(publication)
    return bool(
        ingestion is not None
        and ingestion.get("pdf_linked") is True
        and ingestion.get("json_source_paths") == []
    )


def _has_non_filename_title(publication: ResearchPublication) -> bool:
    """Treat an explicitly filename-derived title as unresolved for DQ-04."""
    if publication.title is None:
        return False
    ingestion = _ingestion_lineage(publication)
    return ingestion is None or ingestion.get("title_source") != "filename"


def _ingestion_lineage(publication: ResearchPublication) -> Mapping[str, object] | None:
    """Return validated-enough ingestion lineage from untrusted raw metadata."""
    ingestion = publication.raw_metadata.get("_ingestion")
    if not isinstance(ingestion, Mapping):
        return None
    return ingestion


def _queue_record(
    verdict: PublicationQualityVerdict,
    schema_version: str,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "publication_id": verdict.publication_id,
        "status": verdict.status.value,
        "requested_actions": list(_REMEDIATION_ACTIONS[verdict.status]),
        "thresholds_version": verdict.thresholds_version,
        "reasons": verdict.reasons,
        "measurements": verdict.measurements.model_dump(mode="json"),
    }


def _verdict_record(verdict: PublicationQualityVerdict) -> dict[str, object]:
    return {
        "publication_id": verdict.publication_id,
        "status": verdict.status.value,
        "indexable": verdict.status.is_indexable,
        "thresholds_version": verdict.thresholds_version,
        "reasons": verdict.reasons,
        "duplicate_of": verdict.duplicate_of,
        "manual_review_page": verdict.manual_review_page,
        "measurements": verdict.measurements.model_dump(mode="json"),
    }


def _deterministic_json(payload: object, *, indent: int | None = None) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
        sort_keys=True,
    )


def _validate_artifact_directory(path: Path) -> Path:
    resolved = path.resolve()
    data_directory = (_REPOSITORY_ROOT / "data").resolve()
    if resolved == data_directory or data_directory in resolved.parents:
        raise ValueError("quality artifacts must never be written under data/")
    if "artifacts" not in resolved.parts:
        raise ValueError("quality artifact output path must be under an artifacts/ directory")
    return resolved


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8", newline="\n")
    temporary_path.replace(path)

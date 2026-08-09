"""Operational quality gate regression tests for DQ-01 through DQ-04.

The synthetic bodies reproduce measured corpus bands without reading ``data/``:

- ``국방논단``: U+0001 at 1.7-5.0%
- DQ-03 damaged report: mixed C0 controls at about 40%
- sparse U+0007 documents: under the 1% corruption threshold
- page density: 1,200 characters/page, close to the measured p50 of 1,154
- DQ-04 filenames: 250 UTF-8 bytes, inside the observed 240-255 byte band
"""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import (
    PublicationPage,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.domain.quality import (
    DEFAULT_QUALITY_THRESHOLDS_VERSION,
    PublicationQualityStatus,
    QualityThresholds,
)
from defense_research_agent.evaluation.quality import (
    QUALITY_ARTIFACT_SCHEMA_VERSION,
    DeterministicPublicationQualityGate,
    PublicationQualityArtifactWriter,
    select_default_index_publications,
)

THRESHOLDS = QualityThresholds(thresholds_version=DEFAULT_QUALITY_THRESHOLDS_VERSION)


def _publication(
    publication_id: str = "pub-1",
    *,
    title: str | None = None,
    local_path: str | None = None,
) -> ResearchPublication:
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=PublicationType.RESEARCH_REPORT,
        title=title,
        local_path=local_path,
    )


def _page(text: str, page_number: int = 1) -> PublicationPage:
    return PublicationPage(
        page_number=page_number,
        text=text,
        provenance=ExtractionProvenance(
            parser_name="fake-quality-parser",
            parser_version="1.0.0",
            source_checksum="f" * 64,
        ),
    )


_UNIT = "국방정책 연구 본문입니다. "
_UNIT_WITH_U0001 = _UNIT.replace(" 연구", "\x01연구")

CLEAN_BODY = _UNIT * 80
"""1,200 characters and 1,200 characters/page, near the measured p50 density."""

FORUM_U0001_BODY = (_UNIT_WITH_U0001 + _UNIT) * 40
"""3.3% U+0001 before substitution, inside the measured 1.7-5.0% forum band."""

SPARSE_BELL_BODY = (_UNIT * 13 + "\x07") * 6
"""0.5% U+0007, matching the sparse controls observed in 29 documents."""

HEAVILY_CORRUPTED_BODY = ("국방정책 연구" + "\x05\x02\x03\x04\x06") * 100
"""41.7% mixed C0 codes, matching the shape of DQ-03's 39.9% report."""

ENGLISH_BODY = "This report examines defense acquisition policy in depth. " * 25

LONG_FILENAME_PATH = f"data/pdfs/Brief/2024_저자_{'가' * 78}.pdf"
TRUNCATED_JAMO_PATH = f"data/pdfs/Brief/2024_저자_{'가' * 77}ᄌ.pdf"


def test_regression_fixture_bands_match_the_measured_corpus() -> None:
    forum_raw_ratio = FORUM_U0001_BODY.count("\x01") / len(FORUM_U0001_BODY)
    damaged_control_ratio = sum(
        character in {"\x02", "\x03", "\x04", "\x05", "\x06"}
        for character in HEAVILY_CORRUPTED_BODY
    ) / len(HEAVILY_CORRUPTED_BODY)

    assert len(CLEAN_BODY) == 1_200
    assert 0.017 <= forum_raw_ratio <= 0.05
    assert forum_raw_ratio > THRESHOLDS.max_control_character_ratio
    assert 0.38 <= damaged_control_ratio <= 0.42
    assert 240 <= len(Path(LONG_FILENAME_PATH).name.encode("utf-8")) <= 255
    assert 240 <= len(Path(TRUNCATED_JAMO_PATH).name.encode("utf-8")) <= 255


def test_measure_is_threshold_free_and_preserves_original_page_text() -> None:
    gate = DeterministicPublicationQualityGate()
    page = _page(FORUM_U0001_BODY)
    before = page.model_dump_json()

    measurements = gate.measure(_publication(), [page])

    assert measurements.character_count == len(FORUM_U0001_BODY)
    assert measurements.control_character_count == 0
    assert page.model_dump_json() == before
    assert "\x01" in page.text


def test_measure_counts_empty_text_and_page_density_after_substitution() -> None:
    gate = DeterministicPublicationQualityGate()

    measurements = gate.measure(
        _publication(),
        [_page(CLEAN_BODY), _page("", 2), _page("\x01", 3)],
    )

    assert measurements.page_count == 3
    assert measurements.non_empty_page_count == 1
    assert measurements.non_empty_page_ratio == 1 / 3
    assert measurements.mean_characters_per_page == measurements.character_count / 3


def test_printable_ratio_uses_isprintable_separately_from_control_ratio() -> None:
    gate = DeterministicPublicationQualityGate()
    body = ("국방정책연구" * 20 + "\u200b" * 20) * 10

    measurements = gate.measure(_publication(), [_page(body)])
    verdict = gate.evaluate(_publication(), [_page(body)], {})

    assert measurements.control_character_count == 0
    assert measurements.control_character_ratio == 0.0
    assert measurements.printable_ratio < THRESHOLDS.min_printable_ratio
    assert verdict.status is PublicationQualityStatus.CORRUPT_TEXT
    assert verdict.reasons == ["출력 가능 문자 비율 미달"]


def test_clean_publication_is_ready_and_indexable() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(_publication(), [_page(CLEAN_BODY)], {})

    assert verdict.status is PublicationQualityStatus.READY
    assert verdict.status.is_indexable


def test_forum_u0001_band_survives_measurement_only_substitution() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(_publication(), [_page(FORUM_U0001_BODY)], {})

    assert verdict.status is PublicationQualityStatus.READY
    assert verdict.status.is_indexable
    assert verdict.measurements.control_character_count == 0


def test_same_density_in_a_non_substituted_control_is_corrupt() -> None:
    gate = DeterministicPublicationQualityGate()
    body = FORUM_U0001_BODY.replace("\x01", "\x07")

    verdict = gate.evaluate(_publication(), [_page(body)], {})

    assert verdict.status is PublicationQualityStatus.CORRUPT_TEXT
    assert not verdict.status.is_indexable


def test_sparse_control_characters_warn_but_remain_indexable() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(_publication(), [_page(SPARSE_BELL_BODY)], {})

    assert verdict.status is PublicationQualityStatus.WARNING
    assert verdict.status.is_indexable
    assert verdict.measurements.control_character_ratio < 0.01
    assert verdict.reasons == [f"제어문자 {verdict.measurements.control_character_count}개"]


def test_dq03_damaged_report_band_is_excluded() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(_publication(), [_page(HEAVILY_CORRUPTED_BODY)], {})

    assert verdict.status is PublicationQualityStatus.CORRUPT_TEXT
    assert not verdict.status.is_indexable
    assert 0.38 <= verdict.measurements.control_character_ratio <= 0.42


def test_dq02_low_extraction_is_excluded_with_a_reason() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(_publication(), [_page("짧음")], {})

    assert verdict.status is PublicationQualityStatus.LOW_TEXT
    assert not verdict.status.is_indexable
    assert verdict.reasons == ["추출 문자 수 미달"]


def test_orphan_pdf_is_defined_as_having_no_extracted_pages() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(
        _publication(local_path="data/pdfs/국방논단/orphan.pdf"),
        [],
        {},
    )

    assert verdict.status is PublicationQualityStatus.ORPHAN_PDF
    assert verdict.measurements.page_count == 0
    assert not verdict.status.is_indexable


def test_low_korean_ratio_is_manual_review_not_automatic_corruption() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(_publication(), [_page(ENGLISH_BODY)], {})

    assert verdict.status is PublicationQualityStatus.MANUAL_REVIEW
    assert not verdict.status.is_indexable
    assert verdict.reasons == ["한글 비율 미달"]


@pytest.mark.parametrize(
    ("local_path", "expected_reason"),
    [
        (LONG_FILENAME_PATH, "파일명 240바이트 이상이며 표지 제목 없음"),
        (TRUNCATED_JAMO_PATH, "파일명이 불완전 한글 자모로 끝나며 표지 제목 없음"),
    ],
)
def test_dq04_filename_risk_without_a_cover_title_requires_manual_review(
    local_path: str,
    expected_reason: str,
) -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(
        _publication(local_path=local_path),
        [_page(CLEAN_BODY)],
        {},
    )

    assert verdict.status is PublicationQualityStatus.MANUAL_REVIEW
    assert verdict.manual_review_page == 1
    assert verdict.reasons == [expected_reason]


def test_cover_derived_title_resolves_dq04_filename_risk() -> None:
    gate = DeterministicPublicationQualityGate()

    verdict = gate.evaluate(
        _publication(title="표지에서 추출한 전체 제목", local_path=TRUNCATED_JAMO_PATH),
        [_page(CLEAN_BODY)],
        {},
    )

    assert verdict.status is PublicationQualityStatus.READY


def test_korean_policy_abstract_does_not_trip_manual_review() -> None:
    gate = DeterministicPublicationQualityGate()
    body = CLEAN_BODY + ENGLISH_BODY

    verdict = gate.evaluate(_publication(), [_page(body)], {})

    assert verdict.measurements.korean_ratio > THRESHOLDS.min_korean_ratio
    assert verdict.status.is_indexable


def test_mostly_empty_pages_warn_at_a_corpus_observed_density() -> None:
    gate = DeterministicPublicationQualityGate()
    pages = [_page(CLEAN_BODY)] + [_page("", number) for number in range(2, 6)]

    verdict = gate.evaluate(_publication(), pages, {})

    assert verdict.measurements.mean_characters_per_page == 240
    assert verdict.status is PublicationQualityStatus.WARNING
    assert verdict.reasons == ["빈 페이지 비율 높음"]


def test_dq01_duplicate_body_tracks_the_owning_publication() -> None:
    gate = DeterministicPublicationQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-original"}

    verdict = gate.evaluate(_publication("pub-copy"), [_page(CLEAN_BODY)], known)

    assert verdict.status is PublicationQualityStatus.DUPLICATE
    assert verdict.duplicate_of == "pub-original"


def test_re_evaluating_the_owner_is_not_a_duplicate() -> None:
    gate = DeterministicPublicationQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-1"}

    verdict = gate.evaluate(_publication("pub-1"), [_page(CLEAN_BODY)], known)

    assert verdict.status is PublicationQualityStatus.READY


def test_stored_measurements_can_be_rejudged_under_a_new_threshold_version() -> None:
    publication = _publication()
    default_gate = DeterministicPublicationQualityGate()
    measurements = default_gate.measure(publication, [_page(CLEAN_BODY)])
    stricter_gate = DeterministicPublicationQualityGate(
        QualityThresholds(
            thresholds_version="quality-v2-recalibration-test",
            min_character_count=1_201,
        )
    )

    original = default_gate.evaluate(
        publication,
        None,
        {},
        measurements=measurements,
    )
    rejudged = stricter_gate.evaluate(
        publication,
        None,
        {},
        measurements=measurements,
    )

    assert original.measurements == rejudged.measurements
    assert original.status is PublicationQualityStatus.READY
    assert rejudged.status is PublicationQualityStatus.LOW_TEXT
    assert rejudged.thresholds_version == "quality-v2-recalibration-test"


def test_measurement_replay_requires_checksum_when_duplicate_context_exists() -> None:
    gate = DeterministicPublicationQualityGate()
    measurements = gate.measure(_publication(), [_page(CLEAN_BODY)])

    with pytest.raises(ValueError, match="content_checksum is required"):
        gate.evaluate(
            _publication(),
            None,
            {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-original"},
            measurements=measurements,
        )


def test_stored_measurements_replay_duplicate_detection_with_original_checksum() -> None:
    gate = DeterministicPublicationQualityGate()
    publication = _publication("pub-copy")
    measurements = gate.measure(publication, [_page(CLEAN_BODY)])
    checksum = sha256(CLEAN_BODY.encode("utf-8")).hexdigest()

    verdict = gate.evaluate(
        publication,
        None,
        {checksum: "pub-original"},
        measurements=measurements,
        content_checksum=checksum,
    )

    assert verdict.status is PublicationQualityStatus.DUPLICATE
    assert verdict.duplicate_of == "pub-original"


def test_supplied_checksum_must_match_original_page_text() -> None:
    gate = DeterministicPublicationQualityGate()

    with pytest.raises(ValueError, match="does not match"):
        gate.evaluate(
            _publication(),
            [_page(CLEAN_BODY)],
            {},
            content_checksum="0" * 64,
        )


def test_same_input_produces_byte_identical_verdicts() -> None:
    gate = DeterministicPublicationQualityGate()
    publication = _publication()
    pages = [_page(SPARSE_BELL_BODY)]

    first = gate.evaluate(publication, pages, {}).model_dump_json().encode("utf-8")
    second = gate.evaluate(publication, pages, {}).model_dump_json().encode("utf-8")

    assert first == second


def test_all_seven_statuses_are_reachable_as_valid_verdicts() -> None:
    gate = DeterministicPublicationQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-original"}
    blank_pages = [_page(CLEAN_BODY)] + [_page("", number) for number in range(2, 6)]

    reached = {
        gate.evaluate(_publication("orphan"), [], {}).status,
        gate.evaluate(_publication("copy"), [_page(CLEAN_BODY)], known).status,
        gate.evaluate(_publication("low"), [_page("짧음")], {}).status,
        gate.evaluate(_publication("corrupt"), [_page(HEAVILY_CORRUPTED_BODY)], {}).status,
        gate.evaluate(_publication("manual"), [_page(ENGLISH_BODY)], {}).status,
        gate.evaluate(_publication("warning"), blank_pages, {}).status,
        gate.evaluate(_publication("ready"), [_page(CLEAN_BODY)], {}).status,
    }

    assert reached == set(PublicationQualityStatus)


def test_default_index_selection_is_fail_closed_and_excludes_quality_failures() -> None:
    gate = DeterministicPublicationQualityGate()
    ready = _publication("ready")
    warning = _publication("warning")
    low = _publication("low")
    verdicts = {
        ready.publication_id: gate.evaluate(ready, [_page(CLEAN_BODY)], {}),
        warning.publication_id: gate.evaluate(warning, [_page(SPARSE_BELL_BODY)], {}),
        low.publication_id: gate.evaluate(low, [_page("짧음")], {}),
    }

    selected = select_default_index_publications([low, warning, ready], verdicts)

    assert [publication.publication_id for publication in selected] == ["warning", "ready"]
    with pytest.raises(ValueError, match="missing quality verdict"):
        select_default_index_publications([_publication("unmeasured")], verdicts)


def test_quality_artifacts_are_versioned_complete_and_byte_deterministic(
    tmp_path: Path,
) -> None:
    gate = DeterministicPublicationQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "original"}
    verdicts = [
        gate.evaluate(_publication("ready"), [_page(CLEAN_BODY)], {}),
        gate.evaluate(_publication("warning"), [_page(SPARSE_BELL_BODY)], {}),
        gate.evaluate(_publication("low"), [_page("짧음")], {}),
        gate.evaluate(_publication("corrupt"), [_page(HEAVILY_CORRUPTED_BODY)], {}),
        gate.evaluate(_publication("orphan"), [], {}),
        gate.evaluate(_publication("manual"), [_page(ENGLISH_BODY)], {}),
        gate.evaluate(_publication("duplicate"), [_page(CLEAN_BODY)], known),
    ]
    writer = PublicationQualityArtifactWriter(tmp_path / "artifacts" / "quality")

    paths = writer.write(verdicts, gate.thresholds)
    first_queue = paths.reextract_ocr_queue.read_bytes()
    first_report = paths.failure_report.read_bytes()
    writer.write(list(reversed(verdicts)), gate.thresholds)

    assert paths.reextract_ocr_queue.read_bytes() == first_queue
    assert paths.failure_report.read_bytes() == first_report

    queue = [json.loads(line) for line in first_queue.decode("utf-8").splitlines()]
    assert [record["publication_id"] for record in queue] == ["corrupt", "low", "orphan"]
    assert {record["schema_version"] for record in queue} == {QUALITY_ARTIFACT_SCHEMA_VERSION}
    assert queue[-1]["requested_actions"] == ["extract_metadata", "extract_text", "ocr"]

    report = json.loads(first_report)
    assert report["schema_version"] == QUALITY_ARTIFACT_SCHEMA_VERSION
    assert report["thresholds"] == gate.thresholds.model_dump(mode="json")
    assert report["total_publications"] == 7
    assert report["indexable_publications"] == 2
    assert report["excluded_publications"] == 5
    assert report["queue_entries"] == 3
    assert set(report["status_counts"]) == {status.value for status in PublicationQualityStatus}
    assert len(report["findings"]) == 6


def test_artifact_writer_rejects_non_artifact_paths_and_mixed_threshold_versions(
    tmp_path: Path,
) -> None:
    gate = DeterministicPublicationQualityGate()
    verdict = gate.evaluate(_publication(), [_page(CLEAN_BODY)], {})

    with pytest.raises(ValueError, match="under an artifacts"):
        PublicationQualityArtifactWriter(tmp_path / "quality")
    with pytest.raises(ValueError, match="never be written under data"):
        PublicationQualityArtifactWriter(Path("data") / "artifacts" / "quality")

    writer = PublicationQualityArtifactWriter(tmp_path / "artifacts" / "quality")
    with pytest.raises(ValueError, match="thresholds_version"):
        writer.write(
            [verdict],
            QualityThresholds(thresholds_version="quality-v2-recalibration-test"),
        )

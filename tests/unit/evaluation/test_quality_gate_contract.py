"""Quality gate interface test, exercised through a fake gate.

The fake computes control-character and script ratios for real rather than
hardcoding clean values. ``DATA_QUALITY_REPORT.md`` DQ-03 records that 192 of
370 documents carry C0/C1 control characters — ``국방논단`` 100/100 and
``국방정책연구`` 59/59 — with ``U+0001`` frequently standing in for a space. A
fixture that assumes ``control_character_count=0`` would represent almost none
of the corpus, so the fixtures below carry those characters.
"""

from collections.abc import Mapping, Sequence
from hashlib import sha256

from defense_research_agent.domain import (
    PublicationPage,
    PublicationQualityStatus,
    PublicationQualityVerdict,
    PublicationType,
    QualityMeasurements,
    QualityThresholds,
    ResearchPublication,
)
from defense_research_agent.evaluation import PublicationQualityGate

THRESHOLDS = QualityThresholds(
    thresholds_version="quality-v0-provisional",
    min_character_count=10,
    min_non_empty_page_ratio=0.5,
    min_printable_ratio=0.9,
    min_korean_ratio=0.1,
    max_control_character_ratio=0.05,
)

ALLOWED_CONTROL = {"\n", "\t", "\r"}


def _is_control(character: str) -> bool:
    if character in ALLOWED_CONTROL:
        return False
    code = ord(character)
    return code < 0x20 or 0x7F <= code <= 0x9F


def _is_korean(character: str) -> bool:
    return "가" <= character <= "힣"


class FakeQualityGate(PublicationQualityGate):
    """Deterministic gate implementing the measure/evaluate split."""

    @property
    def thresholds(self) -> QualityThresholds:
        return THRESHOLDS

    def measure(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> QualityMeasurements:
        text = "".join(page.text for page in pages)
        controls = sum(1 for character in text if _is_control(character))
        koreans = sum(1 for character in text if _is_korean(character))
        printable = len(text) - controls
        return QualityMeasurements(
            character_count=len(text),
            page_count=len(pages),
            non_empty_page_count=sum(1 for page in pages if page.text.strip()),
            control_character_count=controls,
            printable_ratio=printable / len(text) if text else 0.0,
            korean_ratio=koreans / len(text) if text else 0.0,
        )

    def evaluate(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        known_content_checksums: Mapping[str, str],
    ) -> PublicationQualityVerdict:
        measurements = self.measure(publication, pages)
        limits = self.thresholds

        def verdict(
            status: PublicationQualityStatus,
            *reasons: str,
            duplicate_of: str | None = None,
        ) -> PublicationQualityVerdict:
            return PublicationQualityVerdict(
                publication_id=publication.publication_id,
                status=status,
                measurements=measurements,
                thresholds_version=limits.thresholds_version,
                reasons=list(reasons),
                duplicate_of=duplicate_of,
            )

        if not pages:
            return verdict(PublicationQualityStatus.ORPHAN_PDF, "추출된 페이지 없음")

        body = "".join(page.text for page in pages)
        owner = known_content_checksums.get(sha256(body.encode("utf-8")).hexdigest())
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
        if measurements.non_empty_page_ratio < limits.min_non_empty_page_ratio:
            return verdict(PublicationQualityStatus.WARNING, "빈 페이지 비율 높음")
        if measurements.control_character_count > 0:
            return verdict(
                PublicationQualityStatus.WARNING,
                f"제어문자 {measurements.control_character_count}개",
            )
        return verdict(PublicationQualityStatus.READY)


def _publication(publication_id: str = "pub-1") -> ResearchPublication:
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=PublicationType.RESEARCH_REPORT,
    )


def _page(text: str, page_number: int = 1) -> PublicationPage:
    return PublicationPage(page_number=page_number, text=text)


CLEAN_BODY = "국방정책 연구 본문입니다. " * 4
# Sparse U+0001 standing in for a space, the shape DQ-03 records across 국방논단:
# present in the majority of documents but a small share of characters.
LIGHTLY_CORRUPTED = "국방정책\x01연구 본문입니다. " + "국방정책 연구 본문입니다. " * 8
# The opposite extreme DQ-03 also records: one report where control characters
# reach 39.9% of the body.
HEAVILY_CORRUPTED = "국방\x01\x01\x01\x02\x03\x04정책\x05\x06\x07\x08"


def test_measurements_do_not_apply_thresholds() -> None:
    gate = FakeQualityGate()

    measurements = gate.measure(_publication(), [_page(LIGHTLY_CORRUPTED)])

    assert measurements.control_character_count == 1
    assert measurements.control_character_ratio < THRESHOLDS.max_control_character_ratio
    assert measurements.page_count == 1


def test_page_density_and_ratios_are_derived_not_stored() -> None:
    gate = FakeQualityGate()

    measurements = gate.measure(
        _publication(), [_page(CLEAN_BODY), _page("", 2), _page(CLEAN_BODY, 3)]
    )

    assert measurements.page_count == 3
    assert measurements.non_empty_page_ratio == 2 / 3
    assert measurements.mean_characters_per_page == measurements.character_count / 3
    assert measurements.control_character_ratio == 0.0


def test_clean_publication_is_ready_and_indexable() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page(CLEAN_BODY)], {})

    assert verdict.status is PublicationQualityStatus.READY
    assert verdict.status.is_indexable


def test_corpus_typical_control_characters_warn_but_stay_indexable() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page(LIGHTLY_CORRUPTED)], {})

    assert verdict.status is PublicationQualityStatus.WARNING
    assert verdict.status.is_indexable
    assert verdict.measurements.control_character_count > 0
    assert verdict.reasons == [f"제어문자 {verdict.measurements.control_character_count}개"]


def test_heavy_control_character_density_is_excluded_as_corrupt() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page(HEAVILY_CORRUPTED)], {})

    assert verdict.status is PublicationQualityStatus.CORRUPT_TEXT
    assert not verdict.status.is_indexable


def test_low_text_publication_is_excluded_with_a_reason() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page("짧음")], {})

    assert verdict.status is PublicationQualityStatus.LOW_TEXT
    assert not verdict.status.is_indexable
    assert verdict.reasons == ["추출 문자 수 미달"]


def test_publication_without_extracted_pages_is_an_orphan() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [], {})

    assert verdict.status is PublicationQualityStatus.ORPHAN_PDF
    assert not verdict.status.is_indexable


def test_low_korean_ratio_goes_to_manual_review() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(
        _publication(), [_page("Abstract. This report examines defense policy.")], {}
    )

    assert verdict.status is PublicationQualityStatus.MANUAL_REVIEW
    assert not verdict.status.is_indexable


def test_mostly_empty_pages_produce_a_warning() -> None:
    gate = FakeQualityGate()
    pages = [_page(CLEAN_BODY), _page("", 2), _page("  ", 3), _page("\n", 4)]

    verdict = gate.evaluate(_publication(), pages, {})

    assert verdict.status is PublicationQualityStatus.WARNING
    assert verdict.reasons == ["빈 페이지 비율 높음"]


def test_duplicate_body_is_traced_to_the_publication_that_owns_it() -> None:
    gate = FakeQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-original"}

    verdict = gate.evaluate(_publication("pub-copy"), [_page(CLEAN_BODY)], known)

    assert verdict.status is PublicationQualityStatus.DUPLICATE
    assert verdict.duplicate_of == "pub-original"


def test_re_evaluating_the_same_publication_is_not_a_duplicate() -> None:
    gate = FakeQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-1"}

    verdict = gate.evaluate(_publication("pub-1"), [_page(CLEAN_BODY)], known)

    assert verdict.status is PublicationQualityStatus.READY


def test_every_verdict_records_the_threshold_version_that_produced_it() -> None:
    gate = FakeQualityGate()

    for pages in ([], [_page("짧음")], [_page(CLEAN_BODY)], [_page(LIGHTLY_CORRUPTED)]):
        verdict = gate.evaluate(_publication(), pages, {})
        assert verdict.thresholds_version == "quality-v0-provisional"


def test_the_gate_reaches_every_status_it_claims_to_compute() -> None:
    gate = FakeQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-original"}

    reached = {
        gate.evaluate(_publication(), [], {}).status,
        gate.evaluate(_publication("pub-copy"), [_page(CLEAN_BODY)], known).status,
        gate.evaluate(_publication(), [_page("짧음")], {}).status,
        gate.evaluate(_publication(), [_page(HEAVILY_CORRUPTED)], {}).status,
        gate.evaluate(_publication(), [_page("Abstract only, in English.")], {}).status,
        gate.evaluate(_publication(), [_page(LIGHTLY_CORRUPTED)], {}).status,
        gate.evaluate(_publication(), [_page(CLEAN_BODY)], {}).status,
    }

    assert reached == set(PublicationQualityStatus)

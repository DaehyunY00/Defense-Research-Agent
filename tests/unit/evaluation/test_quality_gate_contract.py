"""Quality gate interface test, exercised through a fake gate.

Fixtures sit at the control-character densities actually measured over the
corpus by ``scripts/measure_quality_thresholds.py``, and the gate runs against
the production thresholds rather than a relaxed copy. The measured bands are:

- ``국방논단`` carries ``U+0001`` at 1.7-5.0% of body characters, in all 100 issues
- one ``연구보고서`` is genuinely corrupt at 39.9% mixed C0 codes
- ``U+0007`` appears in 29 documents at well under 1%

Those three bands are what the gate has to separate, so they are what the
fixtures reproduce.
"""

from collections.abc import Mapping, Sequence
from hashlib import sha256

from defense_research_agent.domain import (
    CONTROL_CHARACTER_SUBSTITUTIONS,
    DEFAULT_QUALITY_THRESHOLDS_VERSION,
    ExtractionProvenance,
    PublicationPage,
    PublicationQualityStatus,
    PublicationQualityVerdict,
    PublicationType,
    QualityMeasurements,
    QualityThresholds,
    ResearchPublication,
)
from defense_research_agent.evaluation import PublicationQualityGate

# The production defaults, not a relaxed copy. A gate test that loosens the
# thresholds it is meant to exercise proves nothing about the shipped behaviour.
THRESHOLDS = QualityThresholds(thresholds_version=DEFAULT_QUALITY_THRESHOLDS_VERSION)

ALLOWED_CONTROL = frozenset({"\n", "\t", "\r"})


def _substitute(text: str) -> str:
    for source, replacement in CONTROL_CHARACTER_SUBSTITUTIONS.items():
        text = text.replace(source, replacement)
    return text


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
        # Substitutions apply to measurement only. Stored page text is untouched.
        text = _substitute("".join(page.text for page in pages))
        controls = sum(1 for character in text if _is_control(character))
        koreans = sum(1 for character in text if _is_korean(character))
        unprintable = sum(
            1
            for character in text
            if not character.isprintable() and character not in ALLOWED_CONTROL
        )
        return QualityMeasurements(
            character_count=len(text),
            page_count=len(pages),
            non_empty_page_count=sum(1 for page in pages if page.text.strip()),
            control_character_count=controls,
            printable_ratio=(len(text) - unprintable) / len(text) if text else 0.0,
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
"""1,200 characters, no control characters."""

FORUM_U0001_BODY = (_UNIT_WITH_U0001 + _UNIT) * 40
"""3.3% U+0001 before substitution — the measured ``국방논단`` band (1.7-5.0%)."""

SPARSE_BELL_BODY = (_UNIT * 13 + "\x07") * 6
"""0.5% U+0007, the density measured in the 29 documents that carry it."""

HEAVILY_CORRUPTED_BODY = ("국방정책 연구" + "\x05\x02\x03\x04\x06") * 100
"""41.7% mixed C0 codes, the shape of the single 39.9% report DQ-03 identifies."""

ENGLISH_BODY = "This report examines defense acquisition policy in depth. " * 25


def test_the_forum_band_is_actually_in_the_measured_range() -> None:
    """Guard the fixture itself: a drifting fixture would silently weaken the suite."""
    raw_ratio = FORUM_U0001_BODY.count("\x01") / len(FORUM_U0001_BODY)

    assert 0.017 <= raw_ratio <= 0.05
    assert raw_ratio > THRESHOLDS.max_control_character_ratio


def test_measurements_do_not_apply_thresholds() -> None:
    gate = FakeQualityGate()

    measurements = gate.measure(_publication(), [_page(SPARSE_BELL_BODY)])

    assert measurements.control_character_count == 6
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


def test_forum_u0001_density_survives_because_it_is_substituted_first() -> None:
    """The 100 ``국방논단`` issues must not be discarded over a space substitute."""
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page(FORUM_U0001_BODY)], {})

    assert verdict.status is PublicationQualityStatus.READY
    assert verdict.status.is_indexable
    assert verdict.measurements.control_character_count == 0


def test_the_same_density_in_a_non_substituted_code_is_rejected() -> None:
    """Only U+0001 is a known space substitute; the same density of U+0007 is not."""
    gate = FakeQualityGate()
    body = FORUM_U0001_BODY.replace("\x01", "\x07")

    verdict = gate.evaluate(_publication(), [_page(body)], {})

    assert verdict.status is PublicationQualityStatus.CORRUPT_TEXT
    assert not verdict.status.is_indexable


def test_sparse_control_characters_warn_but_stay_indexable() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page(SPARSE_BELL_BODY)], {})

    assert verdict.status is PublicationQualityStatus.WARNING
    assert verdict.status.is_indexable
    assert verdict.measurements.control_character_ratio < 0.01
    assert verdict.reasons == [f"제어문자 {verdict.measurements.control_character_count}개"]


def test_the_genuinely_corrupt_report_is_excluded() -> None:
    gate = FakeQualityGate()

    verdict = gate.evaluate(_publication(), [_page(HEAVILY_CORRUPTED_BODY)], {})

    assert verdict.status is PublicationQualityStatus.CORRUPT_TEXT
    assert not verdict.status.is_indexable
    assert verdict.measurements.control_character_ratio > 0.3


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

    verdict = gate.evaluate(_publication(), [_page(ENGLISH_BODY)], {})

    assert verdict.status is PublicationQualityStatus.MANUAL_REVIEW
    assert not verdict.status.is_indexable


def test_korean_abstract_sections_do_not_trip_the_korean_threshold() -> None:
    """``국방정책연구`` carries an English Abstract in all 59 issues."""
    gate = FakeQualityGate()
    body = CLEAN_BODY + ENGLISH_BODY

    verdict = gate.evaluate(_publication(), [_page(body)], {})

    assert verdict.measurements.korean_ratio > THRESHOLDS.min_korean_ratio
    assert verdict.status.is_indexable


def test_mostly_empty_pages_produce_a_warning() -> None:
    gate = FakeQualityGate()
    pages = [_page(CLEAN_BODY)] + [_page("", number) for number in range(2, 6)]

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


def test_every_verdict_records_the_calibrated_threshold_version() -> None:
    gate = FakeQualityGate()

    for pages in ([], [_page("짧음")], [_page(CLEAN_BODY)], [_page(SPARSE_BELL_BODY)]):
        verdict = gate.evaluate(_publication(), pages, {})
        assert verdict.thresholds_version == DEFAULT_QUALITY_THRESHOLDS_VERSION


def test_the_gate_reaches_every_status_it_claims_to_compute() -> None:
    gate = FakeQualityGate()
    known = {sha256(CLEAN_BODY.encode("utf-8")).hexdigest(): "pub-original"}
    blank_pages = [_page(CLEAN_BODY)] + [_page("", number) for number in range(2, 6)]

    reached = {
        gate.evaluate(_publication(), [], {}).status,
        gate.evaluate(_publication("pub-copy"), [_page(CLEAN_BODY)], known).status,
        gate.evaluate(_publication(), [_page("짧음")], {}).status,
        gate.evaluate(_publication(), [_page(HEAVILY_CORRUPTED_BODY)], {}).status,
        gate.evaluate(_publication(), [_page(ENGLISH_BODY)], {}).status,
        gate.evaluate(_publication(), blank_pages, {}).status,
        gate.evaluate(_publication(), [_page(CLEAN_BODY)], {}).status,
    }

    assert reached == set(PublicationQualityStatus)

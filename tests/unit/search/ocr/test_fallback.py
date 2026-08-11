"""Tests for deterministic page OCR eligibility, adoption, and provenance."""

from hashlib import sha256

import pytest

from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import (
    PublicationPage,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.domain.quality import PublicationQualityStatus
from defense_research_agent.search.chunking import DeterministicPageChunker
from defense_research_agent.search.ocr import (
    FakeOcrFixture,
    FakeOcrProvider,
    OcrDecisionCode,
    OcrFallbackBoundary,
    OcrFallbackPolicy,
    OcrNeedReason,
    OcrPageInput,
)
from defense_research_agent.search.parsers.base import (
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)

SOURCE_CHECKSUM = "a" * 64
BASE_PROVENANCE = ExtractionProvenance(
    parser_name="base-pdf",
    parser_version="1.0.0",
    source_checksum=SOURCE_CHECKSUM,
)


def _page(page_number: int, text: str) -> PublicationPage:
    return PublicationPage(
        page_number=page_number,
        text=text,
        provenance=BASE_PROVENANCE,
    )


def _page_input(page_number: int, image_bytes: bytes | None = None) -> OcrPageInput:
    rendered = image_bytes if image_bytes is not None else f"page-{page_number}".encode()
    return OcrPageInput(
        page_number=page_number,
        image_bytes=rendered,
        input_checksum=sha256(rendered).hexdigest(),
    )


def _result(
    *,
    pages: list[PublicationPage] | None = None,
    failures: list[ParserFailure] | None = None,
    requires_ocr: bool = False,
) -> ParseResult:
    resolved_pages = [_page(1, "기본 추출 본문")] if pages is None else pages
    resolved_failures = [] if failures is None else failures
    return ParseResult(
        provenance=BASE_PROVENANCE,
        pages=resolved_pages,
        failures=resolved_failures,
        requires_ocr=requires_ocr,
    )


def _empty_failure(page_number: int) -> ParserFailure:
    return ParserFailure(
        code=ParserErrorCode.EMPTY_PAGE,
        message="page has no extractable body text",
        page_number=page_number,
    )


def test_parser_signal_requires_a_page_scoped_empty_page_failure() -> None:
    policy = OcrFallbackPolicy()
    parse_result = _result(failures=[_empty_failure(2)], requires_ocr=True)

    assert policy.document_requires_ocr(parse_result)
    assert policy.page_requires_ocr(parse_result, 2)
    assert not policy.page_requires_ocr(parse_result, 1)
    assert not policy.page_requires_ocr(_result(failures=[_empty_failure(2)]), 2)


@pytest.mark.parametrize(
    "status",
    [
        PublicationQualityStatus.LOW_TEXT,
        PublicationQualityStatus.CORRUPT_TEXT,
        PublicationQualityStatus.ORPHAN_PDF,
    ],
)
def test_remediation_queue_statuses_allow_page_attempts_without_parser_signal(
    status: PublicationQualityStatus,
) -> None:
    policy = OcrFallbackPolicy()
    parse_result = _result()

    assert policy.document_requires_ocr(parse_result, status)
    assert policy.page_requires_ocr(parse_result, 1, status)
    assert policy.need_reasons(parse_result, 1, status) == (
        OcrNeedReason.QUALITY_REMEDIATION_STATUS,
    )


@pytest.mark.parametrize(
    "status",
    [
        None,
        PublicationQualityStatus.READY,
        PublicationQualityStatus.WARNING,
        PublicationQualityStatus.MANUAL_REVIEW,
        PublicationQualityStatus.DUPLICATE,
    ],
)
def test_other_document_states_do_not_independently_require_ocr(
    status: PublicationQualityStatus | None,
) -> None:
    policy = OcrFallbackPolicy()
    parse_result = _result()

    assert not policy.document_requires_ocr(parse_result, status)
    assert not policy.page_requires_ocr(parse_result, 1, status)


def test_not_required_page_does_not_call_provider_or_change_base_page() -> None:
    page_input = _page_input(1)
    provider = FakeOcrProvider({})

    result = OcrFallbackBoundary(provider).apply(_result(), [page_input])

    assert result.pages == [_page(1, "기본 추출 본문")]
    assert not result.document_eligible
    assert not result.requires_ocr
    assert len(result.decisions) == 1
    assert result.decisions[0].decision is OcrDecisionCode.NOT_REQUIRED
    assert result.decisions[0].ocr_result is None


def test_strictly_better_ocr_is_adopted_and_preserves_evidence() -> None:
    page_input = _page_input(1)
    raw_ocr_text = "  더 길고 완전한 OCR 원문\r\n둘째 줄  "
    provider = FakeOcrProvider(
        {page_input.input_checksum: FakeOcrFixture.success(raw_ocr_text, 0.93)}
    )

    result = OcrFallbackBoundary(provider).apply(
        _result(pages=[_page(1, "짧음")]),
        [page_input],
        quality_status=PublicationQualityStatus.LOW_TEXT,
    )

    decision = result.decisions[0]
    assert decision.decision is OcrDecisionCode.ADOPTED
    assert decision.ocr_result is not None
    assert decision.ocr_result.text == raw_ocr_text
    assert decision.ocr_result.confidence == 0.93
    assert decision.ocr_result.input_checksum == page_input.input_checksum
    assert decision.ocr_quality is not None
    assert decision.ocr_quality.usable_character_count > (
        decision.baseline_quality.usable_character_count
    )
    assert result.pages[0].text == raw_ocr_text
    assert not result.requires_ocr


@pytest.mark.parametrize(
    ("ocr_text", "expected_usable_count"),
    [
        ("더 짧음", len("더짧음")),
        ("동일 본문", len("동일본문")),
    ],
    ids=["worse", "equal"],
)
def test_worse_or_equal_ocr_is_rejected_but_result_and_reason_are_preserved(
    ocr_text: str,
    expected_usable_count: int,
) -> None:
    base_text = "동일 본문" if ocr_text == "동일 본문" else "훨씬 더 긴 기본 추출 본문"
    page_input = _page_input(1, ocr_text.encode("utf-8"))
    provider = FakeOcrProvider({page_input.input_checksum: FakeOcrFixture.success(ocr_text, 0.99)})

    result = OcrFallbackBoundary(provider).apply(
        _result(pages=[_page(1, base_text)]),
        [page_input],
        quality_status=PublicationQualityStatus.LOW_TEXT,
    )

    decision = result.decisions[0]
    assert decision.decision is OcrDecisionCode.NOT_BETTER_THAN_BASE
    assert decision.reason == "OCR quality is worse than or equal to base extraction"
    assert decision.ocr_result is not None
    assert decision.ocr_result.text == ocr_text
    assert decision.ocr_quality is not None
    assert decision.ocr_quality.usable_character_count == expected_usable_count
    assert result.pages[0].text == base_text
    assert result.requires_ocr


def test_clean_ocr_can_replace_longer_corrupt_base_but_not_lower_quality_text() -> None:
    policy = OcrFallbackPolicy()
    corrupt_base = policy.measure_text("기본" + "\x00" * 30)
    clean_ocr_input = _page_input(1)
    clean_result = FakeOcrProvider(
        {clean_ocr_input.input_checksum: FakeOcrFixture.success("깨끗한 OCR", 0.9)}
    ).recognize_page(clean_ocr_input)
    dirty_ocr_input = _page_input(2)
    dirty_result = FakeOcrProvider(
        {dirty_ocr_input.input_checksum: FakeOcrFixture.success("긴 OCR" + "\x00", 0.9)}
    ).recognize_page(dirty_ocr_input)

    clean_decision, _, _ = policy.adoption_decision(corrupt_base, clean_result)
    dirty_decision, _, _ = policy.adoption_decision(policy.measure_text("기본"), dirty_result)

    assert clean_decision is OcrDecisionCode.ADOPTED
    assert dirty_decision is OcrDecisionCode.BELOW_MINIMUM_PRINTABLE_RATIO


def test_usable_ocr_above_quality_floor_is_better_than_an_empty_page() -> None:
    policy = OcrFallbackPolicy()
    page_input = _page_input(2)
    result = FakeOcrProvider(
        {page_input.input_checksum: FakeOcrFixture.success("가" * 20 + "\ufffd", 0.9)}
    ).recognize_page(page_input)

    decision, quality, _ = policy.adoption_decision(policy.measure_text(""), result)

    assert decision is OcrDecisionCode.ADOPTED
    assert quality is not None
    assert quality.printable_ratio == pytest.approx(20 / 21)


def test_inconsistent_document_signal_remains_unresolved_without_candidate_page() -> None:
    parse_result = _result(requires_ocr=True)
    unrelated_input = _page_input(1)

    result = OcrFallbackBoundary(FakeOcrProvider({})).apply(
        parse_result,
        [unrelated_input],
    )

    assert result.decisions[0].decision is OcrDecisionCode.NOT_REQUIRED
    assert result.document_eligible
    assert result.requires_ocr


def test_below_confidence_success_is_rejected_with_complete_ocr_result() -> None:
    page_input = _page_input(1)
    provider = FakeOcrProvider(
        {page_input.input_checksum: FakeOcrFixture.success("더 긴 OCR 텍스트", 0.49)}
    )

    result = OcrFallbackBoundary(provider).apply(
        _result(pages=[_page(1, "짧음")]),
        [page_input],
        quality_status=PublicationQualityStatus.LOW_TEXT,
    )

    decision = result.decisions[0]
    assert decision.decision is OcrDecisionCode.BELOW_MINIMUM_CONFIDENCE
    assert decision.ocr_result is not None
    assert decision.ocr_result.confidence == 0.49
    assert result.pages[0].text == "짧음"


def test_timeout_on_one_page_does_not_discard_another_page_success() -> None:
    timeout_input = _page_input(2)
    success_input = _page_input(3)
    parse_result = _result(
        failures=[_empty_failure(2), _empty_failure(3)],
        requires_ocr=True,
    )
    provider = FakeOcrProvider(
        {
            timeout_input.input_checksum: FakeOcrFixture.timeout(),
            success_input.input_checksum: FakeOcrFixture.success("3페이지 OCR 본문", 0.96),
        }
    )

    result = OcrFallbackBoundary(provider).apply(
        parse_result,
        [timeout_input, success_input],
    )

    assert [(decision.page_number, decision.decision) for decision in result.decisions] == [
        (2, OcrDecisionCode.PROVIDER_FAILURE),
        (3, OcrDecisionCode.ADOPTED),
    ]
    assert result.decisions[0].ocr_result is not None
    assert result.decisions[0].ocr_result.failure is not None
    assert result.decisions[0].ocr_result.failure.code.value == "timeout"
    assert [(page.page_number, page.text) for page in result.pages] == [
        (1, "기본 추출 본문"),
        (3, "3페이지 OCR 본문"),
    ]
    assert result.parser_failures == parse_result.failures
    assert result.requires_ocr


def test_missing_rendered_page_is_preserved_as_an_unresolved_decision() -> None:
    parse_result = _result(failures=[_empty_failure(2)], requires_ocr=True)

    result = OcrFallbackBoundary(FakeOcrProvider({})).apply(parse_result, [])

    assert result.decisions[0].page_number == 2
    assert result.decisions[0].decision is OcrDecisionCode.MISSING_PAGE_INPUT
    assert result.decisions[0].input_checksum is None
    assert result.requires_ocr


def test_adopted_page_points_to_ocr_provider_and_keeps_document_checksum() -> None:
    page_input = _page_input(2)
    parse_result = _result(failures=[_empty_failure(2)], requires_ocr=True)
    provider = FakeOcrProvider(
        {page_input.input_checksum: FakeOcrFixture.success("OCR 대체 페이지", 0.97)}
    )

    result = OcrFallbackBoundary(provider).apply(parse_result, [page_input])
    adopted_page = result.pages[1]

    assert adopted_page.provenance.parser_name == provider.name
    assert adopted_page.provenance.parser_version == provider.version
    assert adopted_page.provenance.source_checksum == SOURCE_CHECKSUM
    assert adopted_page.provenance != BASE_PROVENANCE
    assert result.decisions[0].ocr_result is not None
    assert result.decisions[0].ocr_result.input_checksum == page_input.input_checksum


def test_mixed_base_and_ocr_pages_create_chunk_provenance_boundary() -> None:
    page_input = _page_input(2)
    parse_result = _result(failures=[_empty_failure(2)], requires_ocr=True)
    provider = FakeOcrProvider(
        {page_input.input_checksum: FakeOcrFixture.success("OCR 대체 페이지", 0.97)}
    )
    fallback = OcrFallbackBoundary(provider).apply(parse_result, [page_input])
    publication = ResearchPublication(
        publication_id="pub:ocr-boundary",
        publication_type=PublicationType.KIDA_BRIEF,
    )

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(
        publication,
        fallback.pages,
    )

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (2, 2)]
    assert [chunk.provenance.parser_name for chunk in chunks] == [
        "base-pdf",
        provider.name,
    ]


def test_complete_fallback_result_is_byte_identical_across_runs() -> None:
    first_input = _page_input(1, b"first")
    second_input = _page_input(2, b"second")
    parse_result = _result(
        pages=[_page(1, "짧음")],
        failures=[_empty_failure(2)],
        requires_ocr=True,
    )
    fixtures = {
        first_input.input_checksum: FakeOcrFixture.success("더 긴 첫 페이지 OCR", 0.92),
        second_input.input_checksum: FakeOcrFixture.timeout(),
    }

    first = OcrFallbackBoundary(FakeOcrProvider(fixtures)).apply(
        parse_result,
        [second_input, first_input],
        quality_status=PublicationQualityStatus.LOW_TEXT,
    )
    second = OcrFallbackBoundary(FakeOcrProvider(fixtures)).apply(
        parse_result,
        [second_input, first_input],
        quality_status=PublicationQualityStatus.LOW_TEXT,
    )

    assert first.model_dump_json().encode("utf-8") == second.model_dump_json().encode("utf-8")


def test_duplicate_page_inputs_are_rejected_before_provider_calls() -> None:
    page_input = _page_input(1)

    with pytest.raises(ValueError, match="must not repeat"):
        OcrFallbackBoundary(FakeOcrProvider({})).apply(
            _result(),
            [page_input, page_input],
        )


@pytest.mark.parametrize(
    ("name", "value", "error_type"),
    [
        ("minimum_confidence", -0.1, ValueError),
        ("minimum_confidence", 1.1, ValueError),
        ("minimum_printable_ratio", True, TypeError),
    ],
)
def test_invalid_policy_thresholds_are_rejected(
    name: str,
    value: float,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        OcrFallbackPolicy(**{name: value})

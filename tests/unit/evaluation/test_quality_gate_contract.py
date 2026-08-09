"""Quality gate interface test, exercised through a fake gate."""

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

THRESHOLDS = QualityThresholds(thresholds_version="quality-v1", min_character_count=10)


class FakeQualityGate(PublicationQualityGate):
    """Applies only the character-count and duplicate rules."""

    @property
    def thresholds(self) -> QualityThresholds:
        return THRESHOLDS

    def measure(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> QualityMeasurements:
        text = "".join(page.text for page in pages)
        non_empty = sum(1 for page in pages if page.text.strip())
        return QualityMeasurements(
            character_count=len(text),
            page_count=len(pages),
            non_empty_page_count=non_empty,
            control_character_count=0,
            printable_ratio=1.0,
            korean_ratio=1.0,
        )

    def evaluate(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        known_content_checksums: Mapping[str, str],
    ) -> PublicationQualityVerdict:
        measurements = self.measure(publication, pages)
        body = "".join(page.text for page in pages)
        checksum = sha256(body.encode("utf-8")).hexdigest()

        owner = known_content_checksums.get(checksum)
        if owner is not None and owner != publication.publication_id:
            return PublicationQualityVerdict(
                publication_id=publication.publication_id,
                status=PublicationQualityStatus.DUPLICATE,
                measurements=measurements,
                thresholds_version=self.thresholds.thresholds_version,
                reasons=["동일 본문 checksum"],
                duplicate_of=owner,
            )
        if measurements.character_count < self.thresholds.min_character_count:
            return PublicationQualityVerdict(
                publication_id=publication.publication_id,
                status=PublicationQualityStatus.LOW_TEXT,
                measurements=measurements,
                thresholds_version=self.thresholds.thresholds_version,
                reasons=["추출 문자 수 미달"],
            )
        return PublicationQualityVerdict(
            publication_id=publication.publication_id,
            status=PublicationQualityStatus.READY,
            measurements=measurements,
            thresholds_version=self.thresholds.thresholds_version,
        )


def _publication(publication_id: str = "pub-1") -> ResearchPublication:
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=PublicationType.RESEARCH_REPORT,
    )


def test_measurements_do_not_apply_thresholds() -> None:
    gate = FakeQualityGate()
    pages = [PublicationPage(page_number=1, text="짧음")]

    measurements = gate.measure(_publication(), pages)

    assert measurements.character_count == 2
    assert measurements.page_count == 1


def test_healthy_publication_is_ready_and_indexable() -> None:
    gate = FakeQualityGate()
    pages = [PublicationPage(page_number=1, text="가" * 50)]

    verdict = gate.evaluate(_publication(), pages, {})

    assert verdict.status is PublicationQualityStatus.READY
    assert verdict.status.is_indexable
    assert verdict.thresholds_version == "quality-v1"


def test_low_text_publication_is_excluded_with_a_reason() -> None:
    gate = FakeQualityGate()
    pages = [PublicationPage(page_number=1, text="짧음")]

    verdict = gate.evaluate(_publication(), pages, {})

    assert verdict.status is PublicationQualityStatus.LOW_TEXT
    assert not verdict.status.is_indexable
    assert verdict.reasons == ["추출 문자 수 미달"]


def test_duplicate_body_is_traced_to_the_publication_that_owns_it() -> None:
    gate = FakeQualityGate()
    body = "가" * 50
    pages = [PublicationPage(page_number=1, text=body)]
    known = {sha256(body.encode("utf-8")).hexdigest(): "pub-original"}

    verdict = gate.evaluate(_publication("pub-copy"), pages, known)

    assert verdict.status is PublicationQualityStatus.DUPLICATE
    assert verdict.duplicate_of == "pub-original"


def test_re_evaluating_the_same_publication_is_not_a_duplicate() -> None:
    gate = FakeQualityGate()
    body = "가" * 50
    pages = [PublicationPage(page_number=1, text=body)]
    known = {sha256(body.encode("utf-8")).hexdigest(): "pub-1"}

    verdict = gate.evaluate(_publication("pub-1"), pages, known)

    assert verdict.status is PublicationQualityStatus.READY

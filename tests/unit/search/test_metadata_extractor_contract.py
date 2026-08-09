"""Metadata extractor interface test, exercised through a fake extractor."""

from collections.abc import Sequence
from pathlib import Path

from defense_research_agent.domain import (
    ExtractedMetadataValue,
    ExtractedPublicationMetadata,
    ExtractionProvenance,
    MetadataEvidence,
    MetadataField,
    PublicationPage,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.search import PublicationMetadataExtractor

SOURCE_CHECKSUM = "e" * 64


class FakeCoverPageExtractor(PublicationMetadataExtractor):
    """Reads the title from the first page, and reports failure when absent."""

    @property
    def name(self) -> str:
        return "fake-cover"

    @property
    def version(self) -> str:
        return "0.1.0"

    def extract(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        source_path: Path | None = None,
    ) -> ExtractedPublicationMetadata:
        provenance = ExtractionProvenance(
            parser_name=self.name,
            parser_version=self.version,
            source_checksum=SOURCE_CHECKSUM,
        )
        if not pages:
            return ExtractedPublicationMetadata(
                publication_id=publication.publication_id,
                provenance=provenance,
                values=[
                    ExtractedMetadataValue(
                        field=MetadataField.TITLE,
                        failure_reason="표지 페이지 없음",
                    )
                ],
            )
        cover = pages[0]
        return ExtractedPublicationMetadata(
            publication_id=publication.publication_id,
            provenance=provenance,
            values=[
                ExtractedMetadataValue(
                    field=MetadataField.TITLE,
                    normalized=" ".join(cover.text.split()),
                    evidence=MetadataEvidence(raw_text=cover.text, page_number=cover.page_number),
                    confidence=0.7,
                )
            ],
        )


def _publication() -> ResearchPublication:
    return ResearchPublication(
        publication_id="pub-1",
        publication_type=PublicationType.RESEARCH_REPORT,
    )


def test_cover_page_title_keeps_original_text_as_evidence() -> None:
    extractor = FakeCoverPageExtractor()
    pages = [PublicationPage(page_number=1, text="미래  국방환경  연구")]

    metadata = extractor.extract(_publication(), pages)

    value = metadata.values[0]
    assert value.normalized == "미래 국방환경 연구"
    assert value.evidence is not None
    assert value.evidence.raw_text == "미래  국방환경  연구"
    assert value.evidence.page_number == 1


def test_absent_cover_yields_an_explicit_failure_not_a_guess() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [])

    value = metadata.values[0]
    assert value.normalized is None
    assert value.failure_reason == "표지 페이지 없음"


def test_extractor_identity_is_recorded_in_provenance() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [PublicationPage(page_number=1, text="제목")])

    assert metadata.provenance.parser_name == "fake-cover"
    assert metadata.provenance.parser_version == "0.1.0"

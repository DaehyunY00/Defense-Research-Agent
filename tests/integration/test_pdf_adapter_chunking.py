"""Integration from direct PDF text extraction to citation-aware chunks."""

from hashlib import sha256
from pathlib import Path

from defense_research_agent.domain.publication import PublicationType, ResearchPublication
from defense_research_agent.search.chunking import DeterministicPageChunker
from defense_research_agent.search.parsers import PdfiumPdfParser

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "pdf_pages" / "defense_forum.pdf"


def test_pdf_adapter_pages_create_chunks_traceable_to_original_pdf_pages() -> None:
    source_checksum = sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    parsed = PdfiumPdfParser().parse(FIXTURE_PATH, source_checksum)
    publication = ResearchPublication(
        publication_id="pub:kida:pdf-adapter",
        publication_type=PublicationType.DEFENSE_FORUM,
        title="PDF adapter integration fixture",
    )

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(
        publication,
        parsed.pages,
    )

    assert parsed.failures == []
    assert len(chunks) == 1
    chunk = chunks[0]
    assert (chunk.page_start, chunk.page_end) == (1, 2)
    assert [span.page_number for span in chunk.page_spans] == [1, 2]
    for page, span in zip(parsed.pages, chunk.page_spans, strict=True):
        assert chunk.text[span.start_offset :].startswith(page.text)
        assert chunk.provenance == page.provenance

        citation = f"page {page.page_number}"
        citation_offset = chunk.text.index(citation, span.start_offset)
        matching_spans = [
            candidate
            for candidate in chunk.page_spans
            if candidate.start_offset <= citation_offset < candidate.end_offset
        ]
        assert len(matching_spans) == 1
        assert matching_spans[0].page_number == page.page_number
        assert chunk.text[citation_offset : citation_offset + len(citation)] == citation

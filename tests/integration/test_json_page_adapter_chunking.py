"""Integration from observed JSON page records to citation-aware chunks."""

from hashlib import sha256
from pathlib import Path

from defense_research_agent.domain.publication import PublicationType, ResearchPublication
from defense_research_agent.search.chunking import DeterministicPageChunker
from defense_research_agent.search.parsers import JsonPageParser

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "json_pages" / "observed_document.json"


def test_json_adapter_pages_create_chunks_traceable_to_original_pages() -> None:
    source_checksum = sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    parsed = JsonPageParser().parse(FIXTURE_PATH, source_checksum)
    publication = ResearchPublication(
        publication_id="pub:kida:json-page-adapter",
        publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
        title="JSON 페이지 어댑터 통합 fixture",
    )

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(
        publication,
        parsed.pages,
    )

    assert parsed.failures == []
    assert len(chunks) == 1
    chunk = chunks[0]
    assert (chunk.page_start, chunk.page_end) == (1, 3)
    assert [span.page_number for span in chunk.page_spans] == [1, 2, 3]
    for page, span in zip(parsed.pages, chunk.page_spans, strict=True):
        assert chunk.text[span.start_offset :].startswith(page.text)
        assert chunk.provenance == page.provenance

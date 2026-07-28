"""Strict parsing for immutable normalized publication indexes."""

from defense_research_agent.domain import ResearchPublication

DEFAULT_MAX_CORPUS_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_CORPUS_PUBLICATIONS = 50_000
_MAX_INDEX_LINE_BYTES = 2 * 1024 * 1024


def parse_publication_index(
    payload: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
    max_publications: int = DEFAULT_MAX_CORPUS_PUBLICATIONS,
) -> tuple[ResearchPublication, ...]:
    """Validate a bounded UTF-8 JSONL index without accepting duplicate IDs."""
    if max_bytes <= 0 or max_publications <= 0:
        raise ValueError("corpus limits must be positive")
    if not payload:
        raise ValueError("publication index must not be empty")
    if len(payload) > max_bytes:
        raise ValueError("publication index exceeds the configured byte limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("publication index must be UTF-8") from error

    publications: list[ResearchPublication] = []
    publication_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > _MAX_INDEX_LINE_BYTES:
            raise ValueError(f"publication index line {line_number} exceeds the byte limit")
        try:
            publication = ResearchPublication.model_validate_json(line)
        except ValueError as error:
            raise ValueError(f"invalid ResearchPublication JSON on line {line_number}") from error
        if publication.publication_id in publication_ids:
            raise ValueError(
                f"duplicate publication_id on line {line_number}: {publication.publication_id}"
            )
        publication_ids.add(publication.publication_id)
        publications.append(publication)
        if len(publications) > max_publications:
            raise ValueError("publication index exceeds the configured record limit")
    if not publications:
        raise ValueError("publication index must contain at least one publication")
    return tuple(publications)

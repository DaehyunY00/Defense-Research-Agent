"""Bibliographic metadata extraction interface.

Implementations resolve metadata from extracted pages and, as weaker evidence,
from the source file name. They never guess: an unresolved field is returned as
an explicit failure so downstream consumers can distinguish "absent from the
document" from "not attempted".
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from defense_research_agent.domain.common import Label
from defense_research_agent.domain.metadata import ExtractedPublicationMetadata
from defense_research_agent.domain.publication import (
    PublicationPage,
    ResearchPublication,
)


class PublicationMetadataExtractor(ABC):
    """Interface implemented by every metadata extraction strategy."""

    @property
    @abstractmethod
    def name(self) -> Label:
        """Stable extractor name recorded in provenance."""

    @property
    @abstractmethod
    def version(self) -> Label:
        """Extractor version. Bump whenever resolved values can change."""

    @abstractmethod
    def extract(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        source_path: Path | None = None,
    ) -> ExtractedPublicationMetadata:
        """Resolve bibliographic fields from page text and optional file name.

        ``source_path`` is evidence of last resort. When a cover page and a file
        name disagree, the cover page wins and the file-name reading is dropped
        rather than merged.
        """

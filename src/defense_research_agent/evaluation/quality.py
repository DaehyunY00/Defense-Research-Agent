"""Corpus admission quality gate interface.

Measurement and judgement are separate operations on purpose. Measurements are
expensive and depend only on extracted text; judgement is cheap and depends on a
versioned threshold set. Keeping them apart lets a threshold change be replayed
over stored measurements without re-parsing the corpus.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from defense_research_agent.domain.common import Checksum, EntityId
from defense_research_agent.domain.publication import (
    PublicationPage,
    ResearchPublication,
)
from defense_research_agent.domain.quality import (
    PublicationQualityVerdict,
    QualityMeasurements,
    QualityThresholds,
)


class PublicationQualityGate(ABC):
    """Decides which publications may enter the default index."""

    @property
    @abstractmethod
    def thresholds(self) -> QualityThresholds:
        """Versioned thresholds this gate judges against."""

    @abstractmethod
    def measure(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> QualityMeasurements:
        """Compute deterministic text measurements without applying thresholds."""

    @abstractmethod
    def evaluate(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        known_content_checksums: Mapping[Checksum, EntityId],
    ) -> PublicationQualityVerdict:
        """Judge one publication against the current thresholds.

        ``known_content_checksums`` maps already-admitted content checksums to the
        publication that owns them, so a repeat of the same body text is reported
        as ``duplicate`` with a traceable target instead of being indexed twice.
        """

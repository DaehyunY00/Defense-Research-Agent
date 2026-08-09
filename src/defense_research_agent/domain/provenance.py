"""Extraction provenance shared by parser, metadata, and chunk contracts."""

from defense_research_agent.domain.common import Checksum, DomainModel, Label


class ExtractionProvenance(DomainModel):
    """Identifies which extractor produced a derived artifact from which source.

    This model carries no timestamp on purpose. Derived artifacts must be
    byte-reproducible from the same source and the same extractor version, and a
    wall-clock field would break that guarantee. Run time belongs in run logs
    under ``artifacts/``, not in the provenance contract.
    """

    parser_name: Label
    parser_version: Label
    source_checksum: Checksum

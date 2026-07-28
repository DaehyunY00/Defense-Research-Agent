"""Repository interfaces and persistence adapters."""

from defense_research_agent.repositories.base import ResearchPublicationRepository
from defense_research_agent.repositories.gcs_publications import (
    GcsResearchPublicationRepository,
)
from defense_research_agent.repositories.in_memory import (
    InMemoryResearchPublicationRepository,
)
from defense_research_agent.repositories.research_projects import (
    FirestoreResearchProjectRepository,
    InMemoryResearchProjectRepository,
    ResearchProjectAlreadyExistsError,
    ResearchProjectNotFoundError,
    ResearchProjectRepository,
    ResearchProjectStateConflictError,
)
from defense_research_agent.repositories.review_history import ReviewHistoryRepository

__all__ = [
    "FirestoreResearchProjectRepository",
    "GcsResearchPublicationRepository",
    "InMemoryResearchProjectRepository",
    "InMemoryResearchPublicationRepository",
    "ResearchProjectAlreadyExistsError",
    "ResearchProjectNotFoundError",
    "ResearchProjectRepository",
    "ResearchProjectStateConflictError",
    "ResearchPublicationRepository",
    "ReviewHistoryRepository",
]

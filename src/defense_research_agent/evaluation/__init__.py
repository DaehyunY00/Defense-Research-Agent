"""Deterministic scoring, offline pilot metrics, and regression evaluation."""

from defense_research_agent.evaluation.harness import (
    PilotEvaluationHarness,
    count_temporal_leakage,
    effective_publication_year,
    load_publications,
    temporal_backtest_summary,
)

__all__ = [
    "PilotEvaluationHarness",
    "count_temporal_leakage",
    "effective_publication_year",
    "load_publications",
    "temporal_backtest_summary",
]

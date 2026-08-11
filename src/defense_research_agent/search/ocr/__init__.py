"""Page-level OCR contract, deterministic fake, and conservative fallback policy."""

from defense_research_agent.search.ocr.base import (
    OcrErrorCode,
    OcrPageFailure,
    OcrPageInput,
    OcrPageResult,
    OcrProvider,
)
from defense_research_agent.search.ocr.fake import FakeOcrFixture, FakeOcrProvider
from defense_research_agent.search.ocr.fallback import (
    DEFAULT_MINIMUM_CONFIDENCE,
    DEFAULT_MINIMUM_PRINTABLE_RATIO,
    OCR_FALLBACK_POLICY_VERSION,
    OCR_REMEDIATION_STATUSES,
    OcrDecisionCode,
    OcrFallbackBoundary,
    OcrFallbackPolicy,
    OcrFallbackResult,
    OcrNeedReason,
    OcrPageDecision,
    OcrTextQuality,
)

__all__ = [
    "DEFAULT_MINIMUM_CONFIDENCE",
    "DEFAULT_MINIMUM_PRINTABLE_RATIO",
    "OCR_FALLBACK_POLICY_VERSION",
    "OCR_REMEDIATION_STATUSES",
    "FakeOcrFixture",
    "FakeOcrProvider",
    "OcrDecisionCode",
    "OcrErrorCode",
    "OcrFallbackBoundary",
    "OcrFallbackPolicy",
    "OcrFallbackResult",
    "OcrNeedReason",
    "OcrPageDecision",
    "OcrPageFailure",
    "OcrPageInput",
    "OcrPageResult",
    "OcrProvider",
    "OcrTextQuality",
]

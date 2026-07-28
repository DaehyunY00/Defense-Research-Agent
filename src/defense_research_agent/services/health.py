"""Application health reporting."""

import platform
import sys
from dataclasses import dataclass

from defense_research_agent import __version__

PACKAGE_NAME = "defense-research-agent"
PYTHON_REQUIREMENT = ">=3.12,<3.13"


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Serializable health-check result."""

    status: str
    package: str
    version: str
    python: str
    python_requirement: str


def build_health_report() -> HealthReport:
    """Return the current package and Python runtime status."""
    supported_python = sys.version_info[:2] == (3, 12)
    return HealthReport(
        status="ok" if supported_python else "unsupported_python",
        package=PACKAGE_NAME,
        version=__version__,
        python=platform.python_version(),
        python_requirement=PYTHON_REQUIREMENT,
    )

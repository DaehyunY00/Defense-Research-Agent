FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/defense-research-agent

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir ".[sandbox-worker]" \
    && useradd --create-home --uid 10001 sandbox-worker

USER 10001:10001

ENTRYPOINT ["python", "-m", "defense_research_agent.cli.sandbox_worker"]

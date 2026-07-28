FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /opt/defense-research-agent

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 research-agent

USER 10001:10001

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "defense_research_agent.api.app:app", "--host", "0.0.0.0", "--port", "8080"]

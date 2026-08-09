# Defense Research Agent

한국국방연구원 공개 연구자료와 외부 국방·안보 이슈를 결합해 연구주제 후보를
생성·평가·랭킹하고, 최종 선택은 사람이 검토하는 국방정책 연구 지원 시스템이다.

현재 핵심 애플리케이션은 **Topic Discovery**와 **Research Lab**이다. 장기적으로는
문서 수집·검색·RAG·학습·보고서 작성을 공통 기반 위에서 제공하는
**Defense Research Platform**으로 확장한다. 현재 기술 설계는
[Architecture](./docs/ARCHITECTURE.md), 장기 방향은 [Roadmap](./docs/ROADMAP.md),
당장 수행할 개발 작업은 [Implementation Plan](./docs/IMPLEMENTATION_PLAN.md)을
참조한다.

## 현재 기능 상태

표시 기준:

- ✅ 구현 완료: 코드와 오프라인 테스트 경로가 존재한다.
- 🚧 부분 구현 / 실험적: 일부 계약이나 배포 구성은 있으나 전체 품질·운영 검증이 남았다.
- 🧭 계획: 설계 또는 로드맵 단계이며 현재 동작하지 않는다.

| 상태 | 영역 | 현재 범위 |
|---|---|---|
| ✅ | 데이터 정규화 | `data/`를 읽기 전용으로 스캔하고 JSON/PDF 메타데이터를 `ResearchPublication`으로 정규화한다. PDF 본문 신규 추출은 포함하지 않는다. |
| ✅ | 로컬 lexical search | 제목·초록·키워드·본문을 대상으로 결정적 검색과 필터를 제공한다. |
| ✅ | Topic Discovery | 내부 검색 결과와 외부 이슈 신호로 구조화된 연구주제 후보를 생성한다. |
| ✅ | 독립 평가 | 네 평가기가 다른 평가 결과를 보지 않고 구조화된 관찰값을 생성한다. |
| ✅ | 결정적 랭킹 | 가중치·감점·다양성·정렬을 Python 코드가 수행한다. |
| ✅ | Human review | 승인·수정승인·보류·거부와 append-only 이력을 제공하며 자동 승인하지 않는다. |
| ✅ | Research Lab | 7개 역할의 계획, 병렬 전문 연구, 독립 검토와 사람 검토 대기 흐름을 제공한다. |
| ✅ | Model gateway | 오프라인 `FakeModelGateway`와 Pydantic structured output 기반 Claude adapter가 있다. |
| ✅ | 외부 이슈 경계 | fixture provider와 공식 출처 allow-list 기반 Anthropic 검색 adapter가 있다. |
| 🚧 | Document Intelligence | `PublicationChunk` 모델은 있으나 PDF 추출·OCR·페이지/섹션 청킹·품질 게이트 파이프라인은 미완성이다. |
| 🚧 | GCP 실행 경로 | Cloud Run API/Job, Firestore, GCS, Secret Manager와 Terraform 구성이 있으나 실제 환경 smoke test와 운영 관측성이 남았다. |
| 🧭 | Retrieval 확장 | embedding, vector search, hybrid fusion, reranking과 retrieval benchmark는 계획 단계다. |
| 🧭 | Research Copilot / Learning Companion | 공통 retrieval·model runtime 위의 신규 애플리케이션으로 계획돼 있다. |
| 🧭 | Product UI / advanced intelligence | Web UI, notebook, report builder, knowledge graph와 timeline은 후속 단계다. |

현재 지표와 실제로 판정할 수 없는 항목은 [Pilot Result](./docs/PILOT_RESULT.md)에
기록돼 있다. 예시 모델 점수는 코드 경로 검증용 fixture이며 실제 연구 품질을 뜻하지
않는다.

## 아키텍처 요약

```text
read-only data/
    -> normalization and repositories
    -> lexical retrieval + external issue providers
    -> Topic Discovery / Research Lab
    -> deterministic evaluation and ranking
    -> human approval boundary
    -> artifacts/ or explicitly configured GCP stores
```

핵심 경계는 다음과 같다.

- 저장과 조회: `ResearchPublicationRepository`
- 검색 알고리즘: `PublicationSearchAlgorithm`
- 모델 호출: `ModelGateway`
- 외부 이슈: `ExternalIssueSearchProvider`
- 모델 출력: Pydantic 검증 후에만 애플리케이션 상태로 전달
- 점수·정렬·분기: 결정적 Python 코드
- 승인: 사람만 `approved` 상태를 만들 수 있음

세부 컴포넌트, 상태 전이와 알려진 한계는
[Architecture](./docs/ARCHITECTURE.md)에 있다.

## Quick Start

### 1. 환경 구성

요구 사항은 Python 3.12와 [uv](https://docs.astral.sh/uv/)다.

```bash
uv python install 3.12
uv sync --python 3.12 --extra dev
```

필요하면 로컬 환경 파일을 만든다. 실제 `.env`는 Git에 포함하지 않는다.

```bash
cp .env.example .env
```

### 2. Health check

```bash
uv run defense-research-agent health
uv run defense-research-agent health --format json
uv run python -m defense_research_agent health
```

### 3. 읽기 전용 ingest

`data/`는 수정하지 않으며 결과는 `artifacts/`에만 생성한다.

```bash
uv run python -m defense_research_agent.cli.ingest \
  --input data \
  --output artifacts/normalized
```

주요 산출물:

- `artifacts/normalized/publications.jsonl`
- `artifacts/reports/ingestion_report.json`

### 4. 로컬 검색

```bash
uv run python -m defense_research_agent.cli.search \
  --query "국방 인공지능 인력 정책" \
  --type defense_forum \
  --limit 10
```

필터 예시:

```bash
uv run python -m defense_research_agent.cli.search \
  --query "인공지능" \
  --author "곽지희" \
  --start-date 2024-01-01 \
  --end-date 2025-12-31
```

### 5. 오프라인 Topic Discovery 파일럿

```bash
uv run python -m defense_research_agent.cli.run_offline_pilot \
  --run-id pilot-example \
  --candidate-count 5
```

이 실행은 `FakeModelGateway`를 사용하며 `awaiting_review`에서 멈춘다.

```bash
uv run python -m defense_research_agent.cli.review_topics \
  --run-id pilot-example
```

비대화형 단일 결정:

```bash
uv run python -m defense_research_agent.cli.review_topics \
  --run-id pilot-example \
  --candidate-id CANDIDATE_ID \
  --decision approve \
  --reviewer "검토자 이름"
```

### 6. 오프라인 Research Lab 데모

```bash
uv run python -m defense_research_agent.cli.research_lab_demo \
  --project-id lab-demo
```

7개 역할의 구조화 계약과 오케스트레이션을 네트워크 없이 검증한다. 출력 내용은
정책 사실판단이 아니라 fixture다.

### 7. Claude 설정 검증

```bash
export ANTHROPIC_API_KEY="..."
uv run defense-research-agent claude-config --format json
```

이 명령은 설정만 검증하고 외부 API를 호출하거나 키 값을 출력하지 않는다.

### 8. 파일럿 평가

```bash
uv run python -m defense_research_agent.cli.evaluate_pilot \
  --run-id pilot-example \
  --cutoff-year 2024
```

전문가 골든셋이 필요한 지표는 임의로 추정하지 않고 `unavailable`로 기록한다.

### 9. 선택적 GCP 배포

```bash
./deploy/gcp-app/deploy.sh YOUR_GCP_PROJECT_ID
```

이 경로는 GCP 자격증명, 운영자 승인과 별도 검증이 필요하다. 상세 절차는
[GCP application deployment](./deploy/gcp-app/README.md)를 참조한다.

## 검증

기본 테스트는 외부 API credentials 없이 실행 가능해야 한다.

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv run python -c "import defense_research_agent"
uv run defense-research-agent health
```

## 데이터 정책

- `data/`는 변경하지 않는 원본 입력이다.
- 정규화 데이터, 인덱스, 실행 로그와 평가 결과는 `artifacts/`에 기록한다.
- 비밀정보, 실제 `.env`, 토큰과 개인 자격증명을 커밋하지 않는다.
- 외부 콘텐츠와 검증 전 LLM 출력은 신뢰할 수 없는 데이터로 취급한다.

## Documentation Map

| 문서 | 역할 |
|---|---|
| [Architecture](./docs/ARCHITECTURE.md) | 현재 구현의 기술 설계와 경계 |
| [Roadmap](./docs/ROADMAP.md) | Defense Research Platform의 장기 제품·엔지니어링 방향 |
| [Roadmap PDF](./output/pdf/defense-research-platform-roadmap.pdf) | 로드맵의 시각적 요약 |
| [Implementation Plan](./docs/IMPLEMENTATION_PLAN.md) | 지금부터 수행할 우선순위와 개발 backlog |
| [Evaluation](./docs/EVALUATION.md) | ingestion, retrieval, RAG와 model/runtime 평가 체계 |
| [Decisions](./docs/DECISIONS.md) | 주요 아키텍처 결정과 trade-off |
| [Evaluation Criteria](./docs/EVALUATION_CRITERIA.md) | 현재 Topic Discovery 평가·랭킹 공식 |
| [Pilot Result](./docs/PILOT_RESULT.md) | 현재 파일럿 검증 결과와 미측정 항목 |
| [Data Inventory](./docs/DATA_INVENTORY.md) | 원본 코퍼스 구조와 수량 |
| [Data Quality Report](./docs/DATA_QUALITY_REPORT.md) | 관찰된 데이터 품질 위험과 게이트 |
| [Research Lab](./docs/RESEARCH_LAB.md) | 7개 역할과 도구·증거·승인 경계 |
| [Claude Gateway](./docs/CLAUDE_GATEWAY.md) | Claude structured output와 보안 경계 |
| [LangGraph Decision](./docs/AUTOGEN_VS_LANGGRAPH.md) | LangGraph 유지 결정의 상세 비교 |
| [GCP Application](./deploy/gcp-app/README.md) | 사용자 API와 비동기 worker 배포 |
| [GCP Sandbox](./deploy/gcp-sandbox/README.md) | 격리 pytest runner 배포 |
| [Development Rules](./AGENTS.md) | 저장소 개발·안전 규칙 |

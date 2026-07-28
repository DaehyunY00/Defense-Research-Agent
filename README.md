# defense-research-agent

한국국방연구원 공개 연구자료와 향후 연결할 외부 최신 이슈를 결합해 국방정책
연구주제 후보를 생성·평가·추천하는 인간 승인형 멀티 에이전트 시스템이다.

현재 단계는 Python 3.12 프로젝트 골격, 도메인 모델, health-check, 읽기 전용
데이터 정규화, 로컬 검색, fixture 외부 이슈, 연구주제 생성, 네 독립 평가기,
결정적 랭킹·다양성 조정, 인간 승인 중단·재개, 오프라인 평가 하네스와 7개 역할
연구실 오케스트레이션, 격리 코드 검증 계약과 제한된 공개 데이터 분석 샌드박스를
제공한다. Claude 구조화 출력 게이트웨이와 7개 역할별 기본 모델 라우트도 제공한다.
오프라인 데모는 계속 `FakeModelGateway`를 사용한다. GCP worker는 같은 Claude API
키로 공식 출처 웹 검색을 수행하고, 사람이 승인한 내부 코퍼스가 있으면 private GCS의
무결성 검증된 인덱스를 함께 검색한다.

## 디렉터리

```text
defense-research-agent/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── data/                         # 원본 공개 연구자료, 읽기 전용
├── artifacts/                    # 재생성 가능한 파생 결과
├── docs/
├── src/
│   └── defense_research_agent/
│       ├── domain/
│       ├── data/
│       ├── repositories/
│       ├── search/
│       ├── agents/
│       ├── graph/
│       ├── services/
│       ├── evaluation/
│       └── cli/
└── tests/
    ├── unit/
    ├── integration/
    ├── graph/
    └── fixtures/
```

`data/`는 원본 읽기 전용 경로다. 애플리케이션은 이 경로에 결과를 쓰지 않는다.
정규화 데이터, 인덱스, 실행 로그와 평가 결과는 `artifacts/`에 저장하며,
`artifacts/`의 생성 파일은 기본적으로 Git에서 제외한다.

## 요구 사항

- Python 3.12
- 권장 패키지 관리자: [uv](https://docs.astral.sh/uv/)

## 설치

### uv

```bash
uv python install 3.12
uv sync --python 3.12 --extra dev
```

### 표준 venv와 pip

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

필요하면 로컬 환경 파일을 만든다. `.env`는 Git에 포함되지 않는다.

```bash
cp .env.example .env
```

## Health check

```bash
uv run defense-research-agent health
uv run defense-research-agent health --format json
uv run python -m defense_research_agent health
```

정상 환경에서는 패키지 버전, Python 버전, 지원 Python 범위와 `status=ok`를 반환한다.

## 데이터 정규화

`data/`를 재귀적으로 읽고 원본을 변경하지 않은 채 `ResearchPublication` JSONL과
수집 보고서를 생성한다.

```bash
uv run python -m defense_research_agent.cli.ingest \
  --input data \
  --output artifacts/normalized
```

생성 경로:

- `artifacts/normalized/publications.jsonl`
- `artifacts/reports/ingestion_report.json`

현재 Reader는 실제 코퍼스에서 확인된 UTF-8 JSON과 PDF만 지원한다. PDF 본문을
새로 추출하지 않고 문서 JSON의 기존 추출 텍스트를 우선하며, 연결 메타데이터가
없는 PDF는 체크섬·경로·파일명 기반 필드만 정규화한다. 파일별 실패는 보고서에
기록되고 나머지 파일 처리는 계속된다.

## 내부 연구자료 검색

외부 DB나 모델 없이 정규화 JSONL을 메모리에 적재해 제목, 초록, 키워드와 본문을
검색한다.

```bash
uv run python -m defense_research_agent.cli.search \
  --query "국방 인공지능 인력 정책" \
  --type defense_forum \
  --limit 10
```

선택 필터:

```bash
uv run python -m defense_research_agent.cli.search \
  --query "인공지능" \
  --author "곽지희" \
  --start-date 2024-01-01 \
  --end-date 2025-12-31
```

검색 구조, 점수 의미, 현재 한계와 벡터 확장 지점은
[`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)에 기록돼 있다.

## 외부 이슈 Mock Provider

최근 국방·안보 이슈 수집은 현재 실제 인터넷 API 대신
`tests/fixtures/external_issues.json`을 사용하는 Mock Provider로 시험할 수 있다.
Provider 결과는 URL·날짜·기관명을 정규화하고 중복 제거한 뒤 `TopicSignal`로
변환된다. 모든 외부 title과 snippet은 검증 후에도 신뢰할 수 없는 평문으로 취급한다.

실제 Provider 교체 계약, 실패 상태, 출처 우선순위와 환경변수는
[`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md#7-외부-이슈-검색-경계)에
기록돼 있다.

GCP worker는 별도 검색 키 없이 Claude의 서버 측 웹 검색을 사용한다. 검색 도메인은
배포 설정의 공식 기관 allow-list로 제한되고, 실제 검색 결과 URL과 일치하는 인용만
근거로 변환한다. 모델의 일반 답변 문장은 외부 근거로 저장하지 않는다.

## 연구주제 생성 그래프

내부 검색 결과와 외부 `TopicSignal`을 근거로 연구주제 후보를 생성하는
`TopicGenerator`와 `generate_topic_candidates` LangGraph 노드를 제공한다.
기본 경로는 실제 LLM API가 아니라 구조화 응답 fixture를 사용하는
`FakeModelGateway`다.

후보는 근거 ID, 연구질문, 시급성, 기존 연구 연결, 차별성 가설, 공개자료 한계와
추천 산출물 유형을 포함해야 한다. 모델 출력 이후에도 근거 누락·허위 ID·제외 분야·
중복 후보를 결정적 Python 코드로 검사한다. 상세 구조는
[`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md#8-연구주제-생성과-langgraph)를
참조한다.

## 오프라인 파일럿 전체 실행

다음 명령은 정규화 자료와 외부 이슈 fixture를 읽고 `FakeModelGateway` 예시 후보를
생성·평가·랭킹한 뒤 사람 검토 전 상태에서 멈춘다.

```bash
uv run python -m defense_research_agent.cli.run_offline_pilot \
  --run-id pilot-example \
  --candidate-count 5
```

생성 산출물:

- `artifacts/runs/pilot-example/evaluation_results.json`
- `artifacts/runs/pilot-example/ranked_candidates.json`
- `artifacts/runs/pilot-example/run_state.json`

예시 점수는 코드 경로 검증을 위한 fixture 값이며 실제 시스템 성능이나 전문가 판정을
뜻하지 않는다. `run_state.json`은 `human_approved=false`,
`status=awaiting_review`로 저장된다.

### 인간 검토와 같은 실행 재개

CLI는 아직 결정하지 않았거나 보류한 후보를 순서대로 보여준다.

```bash
uv run python -m defense_research_agent.cli.review_topics \
  --run-id pilot-example
```

비대화형 단일 결정도 가능하다.

```bash
uv run python -m defense_research_agent.cli.review_topics \
  --run-id pilot-example \
  --candidate-id CANDIDATE_ID \
  --decision approve \
  --reviewer "검토자 이름"
```

허용 결정은 `approve`, `approve_with_edits`, `hold`, `reject`다. 모든 이력은
`review_history.jsonl`에 추가만 되며, 보류·미검토 후보가 있으면 다시
`awaiting_review`에서 멈춘다. 승인 또는 수정승인 후보가 있고 검토가 완료된 경우에만
`topic_planning_cards.json`을 만든다. 에이전트나 CLI 기본값이 후보를 자동 승인하지
않는다.

## 7개 역할 연구실 데모

메인 연구자, 문헌·최신 이슈·방법론·개발 연구자, 근거 감사자와 비판 연구자가 협업하는
상위 오케스트레이션을 네트워크 없이 실행할 수 있다.

```bash
uv run python -m defense_research_agent.cli.research_lab_demo \
  --project-id lab-demo
```

메인 연구자가 과업을 계획한 뒤 네 전문 연구가 병렬 실행되고, 두 검토 역할이 같은
결과 스냅샷을 독립적으로 검토한다. 최종 결과는 항상 `awaiting_human_review`다.
개발 연구자는 코드·테스트 산출물을 제안할 수 있지만 배포 권한이 없고 모든 제안은
사람 승인이 필요하다.

현재 데모는 기존 `ResearchPublicationRepository`, Mock 외부 이슈 Provider와 결정적
공개 데이터 분석기를 `ResearchToolRuntime`에 연결한다. 계획에서 요청한 도구가 역할
허용 목록에 있는지 검사하고, 내부 발간물 ID·외부 source ID·분석 결과 ID를
`ResearchToolContext`로 모델에 전달한다. 입력 context에 없는 근거 ID를 모델 결과가
인용하면 실행을 실패로 기록한다. 역할 계약, 도구 증거 경계, Claude 교체 경계와 GCP
목표 구조는
[`docs/RESEARCH_LAB.md`](./docs/RESEARCH_LAB.md)에 정리돼 있다.

개발 연구자의 `code_patch`는 원본 프로젝트가 아닌 임시 복사본에만 적용된다. 로컬 기본
실행기는 허용된 PoC 경로의 생성·체크섬 기반 교체와 Python 구문 검사를 수행하고,
unified diff와 검사 로그를 `sandbox_results`에 남긴다. 문자열 명령, 삭제, 원본 반영,
배포는 실행하지 않는다. 로컬 기본 runner의 pytest는 계속 차단되며, GCP 환경에서는
`GcpCloudRunJobValidationRunner`를 주입해 해시가 묶인 GCS 번들을 전용 Cloud Run
Job에서 검사할 수 있다. worker 컨테이너와 최소 권한 Terraform 구성은
[`deploy/gcp-sandbox/README.md`](./deploy/gcp-sandbox/README.md)에 있다. 실제 GCP
리소스 생성과 controller 연결은 운영자가 명시적으로 수행해야 한다.

방법론·데이터 연구자는 `data_analysis_sandbox`를 통해 배포자가 등록한 공개 데이터셋만
분석할 수 있다. 허용 연산은 행 수, 수치 기술통계, 그룹별 건수·평균과 Pearson
상관계수이며, 필터도 열-스칼라 비교만 지원한다. 모델이 코드, SQL, 파일 경로나 URL을
실행 입력으로 넘기는 필드는 존재하지 않는다. 기본 wheel에는 오프라인 경로 검증용
소형 예제 데이터가 포함되며 실제 운영 데이터는 검토 후 같은 스키마의 레지스트리로
교체해야 한다.

## Claude 설정 검증

기본 역할별 모델, timeout과 재시도 설정이 포함되어 있어 Claude 연결에 필요한 필수
설정은 API 키 하나다.

```bash
export ANTHROPIC_API_KEY="..."
uv run defense-research-agent claude-config --format json
```

이 명령은 설정과 역할별 라우트만 검증하며 외부 API를 호출하지 않고 키 값을 출력하지
않는다. 실제 연구실 조립은 `AnthropicRuntimeSettings.from_environment()`와
`build_anthropic_research_lab_agents()`를 사용한다. 기본 모델과 선택 환경변수,
오류·감사 경계는
[`docs/CLAUDE_GATEWAY.md`](./docs/CLAUDE_GATEWAY.md)에 정리돼 있다.

## GCP 사용자 서비스 배포

P020 주 애플리케이션은 비공개 Cloud Run API, 비동기 Cloud Run Job, Firestore 상태,
create-only GCS 결과, 검토된 private GCS 코퍼스와 Secret Manager Claude 키 binding을
제공한다. Git checkout
또는 Cloud Shell에 저장소를 올린 뒤 다음 명령을 실행하면 배포 스크립트가 Claude API
키를 숨김 입력으로 한 번 요청한다.

```bash
./deploy/gcp-app/deploy.sh YOUR_GCP_PROJECT_ID
```

API는 연구 요청을 202로 접수하고 즉시 project ID를 반환한다. 사용자는 상태를 조회한
뒤 `awaiting_human_review` 결과를 읽고 `approve`, `approve_with_edits`, `hold`,
`reject` 중 하나를 명시적으로 제출한다. API는 IAM 인증 사용자에게만 열리며 Claude
키는 worker에만 주입되고 Terraform state에 들어가지 않는다.

`data/`에 공개 자료가 있으면 스크립트는 원본 checksum을 보존한 채 정규화하고, 운영자
승인을 받은 content-addressed 인덱스와 manifest만 별도 private GCS 버킷에 create-only로
업로드한다. 자료가 없거나 승인을 보류하면 내부 검색만 비활성화되고 공식 웹 검색과
7개 역할 연구실은 계속 동작한다. 배포·API 예제와 현재 한계는
[`deploy/gcp-app/README.md`](./deploy/gcp-app/README.md)에 있다.

### 파일럿 품질 평가

```bash
uv run python -m defense_research_agent.cli.evaluate_pilot \
  --run-id pilot-example \
  --cutoff-year 2024
```

결과:

- `artifacts/evaluation/evaluation_summary.json`
- `artifacts/evaluation/evaluation_report.md`
- `artifacts/evaluation/expert_review_template.csv`

전문가 골든셋이 필요한 분류 정확도, Recall@K와 탐지 정확도는 추정하지 않고
`unavailable`로 기록한다. 시점 백테스트는 정확한 발행일이 없을 때 파일명 연도만
사용하며 기준 연도 이후 자료가 입력에 들어갔는지 별도로 계산한다.

## 평가·랭킹 계산

네 평가기는 서로의 결과를 보지 않고 병렬 실행 가능하며, 실패는 제한된 재시도 뒤
후보별 실패로 남는다. Python Ranker의 원점수는 다음 일곱 기준 가중합이다.

```text
raw_score =
  policy_relevance × 0.20
  + timeliness × 0.10
  + novelty × 0.20
  + public_evidence_sufficiency × 0.15
  + policy_impact × 0.15
  + feasibility × 0.10
  + output_fit × 0.10
```

가중치·감점·다양성 설정은 `configs/scoring.json`에서 바꿀 수 있다. 기존 연구 직접
중복, 공식자료 부족, 과도한 범위, 단순 해외사례, 근거 ID·confidence·평가 기준 부족을
감점한다. 다양성 조정은 분야·국가·산출물·연구 시계 반복 감점을 적용하며 끌 수 있다.
원점수, 일반 감점, 다양성 조정과 최종 점수를 모두 보존한다. 정확한 계산 규칙은
[`docs/EVALUATION_CRITERIA.md`](./docs/EVALUATION_CRITERIA.md)에 기록돼 있다.

## 테스트와 정적 검사

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv run python -c "import defense_research_agent"
```

전체 검증:

```bash
uv run pytest \
  && uv run mypy \
  && uv run ruff check . \
  && uv run ruff format --check . \
  && uv run python -c "import defense_research_agent"
```

## 개발 규칙과 데이터 현황

- 개발 규칙: [`AGENTS.md`](./AGENTS.md)
- 데이터 인벤토리: [`docs/DATA_INVENTORY.md`](./docs/DATA_INVENTORY.md)
- 데이터 품질: [`docs/DATA_QUALITY_REPORT.md`](./docs/DATA_QUALITY_REPORT.md)
- 구현 계획: [`docs/IMPLEMENTATION_PLAN.md`](./docs/IMPLEMENTATION_PLAN.md)
- 아키텍처: [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
- 평가 기준: [`docs/EVALUATION_CRITERIA.md`](./docs/EVALUATION_CRITERIA.md)
- 파일럿 결과: [`docs/PILOT_RESULT.md`](./docs/PILOT_RESULT.md)
- 7개 역할 연구실: [`docs/RESEARCH_LAB.md`](./docs/RESEARCH_LAB.md)
- Claude 게이트웨이: [`docs/CLAUDE_GATEWAY.md`](./docs/CLAUDE_GATEWAY.md)
- AutoGen·LangGraph 선택: [`docs/AUTOGEN_VS_LANGGRAPH.md`](./docs/AUTOGEN_VS_LANGGRAPH.md)
- GCP 사용자 서비스: [`deploy/gcp-app/README.md`](./deploy/gcp-app/README.md)

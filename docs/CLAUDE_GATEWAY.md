# Claude 구조화 출력 게이트웨이

## 현재 구현

`AnthropicModelGateway`는 애플리케이션의 `ModelGateway` 계약을 공식 Anthropic Python
SDK에 연결한다. 모든 호출은 SDK의 `messages.parse()`와 해당 요청의 Pydantic
`output_schema`를 사용한다. SDK가 Pydantic 스키마를 Claude의
`output_config.format.type=json_schema` 요청으로 변환하고, 응답을 원래 Pydantic
제약으로 검증한 뒤 애플리케이션이 한 번 더 같은 스키마로 검증한다.

기본 오프라인 CLI와 테스트는 계속 `FakeModelGateway`를 사용한다. Claude 호출은
`AnthropicRuntimeSettings`와 `build_anthropic_research_lab_agents()`를 명시적으로
조립한 운영 경로에서만 발생한다.

## 최소 설정

기본 역할별 라우트, timeout과 재시도 횟수가 코드에 제공되므로 운영에 필요한 값은
하나뿐이다.

```bash
export ANTHROPIC_API_KEY="..."
uv run defense-research-agent claude-config --format json
```

`claude-config`는 키의 존재와 라우트 설정만 검증하며 API를 호출하지 않는다. 키의 실제
값은 어떤 출력에도 포함하지 않는다. GCP에서는 `.env` 파일 대신 Secret Manager의
비밀을 같은 이름의 Cloud Run 환경변수에 연결한다.

## 기본 역할별 라우트

| 역할 | 기본 모델 | 선택 이유 |
|---|---|---|
| 메인 연구자 | `claude-opus-5` | 계획 분해, 충돌 조정과 최종 종합 |
| 문헌 연구자 | `claude-haiku-4-5` | 많은 문헌 항목의 빠른 구조화 처리 |
| 최신 이슈 연구자 | `claude-haiku-4-5` | 짧은 공개 출처의 빠른 정규화·요약 |
| 방법론·데이터 연구자 | `claude-sonnet-5` | 연구설계와 분석 해석의 균형 |
| 개발·PoC 연구자 | `claude-sonnet-5` | 코드 제안과 검증 계획의 비용·성능 균형 |
| 근거 감사자 | `claude-opus-5` | 주장-근거 추적과 누락 근거의 정밀 검토 |
| 비판·레드팀 연구자 | `claude-opus-5` | 반증 시나리오와 대안 설명의 심층 추론 |

모델 alias는 배포 시점의 사용 가능 모델과 자체 평가 결과에 따라 환경변수로 교체할 수
있다.

```text
DEFENSE_RESEARCH_CLAUDE_MODEL_MAIN_RESEARCHER
DEFENSE_RESEARCH_CLAUDE_MODEL_LITERATURE_RESEARCHER
DEFENSE_RESEARCH_CLAUDE_MODEL_CURRENT_ISSUE_RESEARCHER
DEFENSE_RESEARCH_CLAUDE_MODEL_METHODOLOGY_RESEARCHER
DEFENSE_RESEARCH_CLAUDE_MODEL_DEVELOPER_RESEARCHER
DEFENSE_RESEARCH_CLAUDE_MODEL_EVIDENCE_AUDITOR
DEFENSE_RESEARCH_CLAUDE_MODEL_CRITICAL_REVIEWER
```

선택 운영 설정:

```text
DEFENSE_RESEARCH_CLAUDE_TIMEOUT_SECONDS=90
DEFENSE_RESEARCH_CLAUDE_MAX_RETRIES=2
```

## 실패와 보안 경계

- SDK가 429·5xx와 일시적 연결 오류를 설정된 횟수 안에서 처리한다.
- `stop_reason=refusal`은 `ModelGatewayRefusalError`로 분리한다.
- `stop_reason=max_tokens`와 Pydantic 불일치는 `ModelGatewayOutputError`로 분리한다.
- Provider 예외 메시지는 그대로 노출하지 않고 예외 종류만 정제해 반환한다.
- API 키, 프롬프트와 provider 원문은 `AnthropicModelCallAudit`에 기록하지 않는다.
- 감사 레코드는 task type, model ID, schema, 성공 상태, 지연, token 수, request ID와
  허용된 실행 식별자만 보존한다.
- 현재 모델 호환성을 위해 temperature·top-p·top-k는 기본 요청에서 생략한다.
- 구조화 연구 결과, 코드 실행과 데이터 분석은 기존 Python allow-list adapter 경계를
  유지한다. 공식 외부 검색만 Claude 서버 측 `web_search` 도구를 사용하며 허용 도메인,
  호출 횟수와 인용 교차검증은 애플리케이션이 제한한다.

## GCP 배포 연결

P020 worker는 `AnthropicRuntimeSettings.from_environment()`와
`build_anthropic_research_lab_agents()`를 그대로 사용한다. Secret Manager의 숫자
버전을 worker의 `ANTHROPIC_API_KEY`에만 연결하며 API 서비스에는 키 접근 권한을 주지
않는다. 키 값은 deploy 스크립트의 표준입력으로 등록되고 Terraform state에 포함되지
않는다. 최신 이슈 연구자는 같은 공유 SDK client의 공식 출처 웹 검색을 사용하므로
두 번째 검색 키가 필요 없다. 전체 절차는
[`../deploy/gcp-app/README.md`](../deploy/gcp-app/README.md)에 있다.

# 평가·랭킹 기준

## 1. 평가 역할과 독립성

각 평가기는 동일한 후보와 허용된 내부·외부 근거만 받으며 다른 평가기의 점수,
근거 또는 실패를 입력으로 받지 않는다.

| 평가기 | 산출 기준 |
|---|---|
| PolicyRelevanceEvaluator | `policy_relevance`, `timeliness`, `policy_impact` |
| NoveltyEvaluator | `novelty` |
| EvidenceFeasibilityEvaluator | `public_evidence_sufficiency`, `feasibility` |
| OutputFitEvaluator | `output_fit` |

모델 관찰값은 `EvaluationResult`로 검증한다. 입력 후보에 연결되지 않은 근거 ID는
허용하지 않으며, 근거가 없는 60점 초과 결과는 Python에서 60점으로 제한한다.
후보 제목이 기존 발간물 제목과 직접 일치하면 신규성은 20점 이하로 제한한다.

## 2. 원점수

설정의 일곱 가중치 합은 1이어야 한다. 후보의 원점수는 다음과 같다.

```text
raw_score = Σ(criterion_score × criterion_weight)
```

누락된 기준은 0점으로 계산하고 별도 누락 감점을 적용한다. 동일 기준 결과가 여러
개이면 산술평균을 사용한다. 모든 중간값은 원래 정밀도로 계산하고 저장 시 소수점
넷째 자리까지 반올림한다.

## 3. 결정적 감점

`penalized_score = max(0, raw_score - Σ penalties)`로 계산한다. 감점 규칙과 크기는
`configs/scoring.json`에서 변경할 수 있다.

- 기존 발간물 직접 중복
- 공식 공개자료 부족
- 지나치게 광범위한 연구질문
- 단순 해외사례 소개
- 근거 ID 부족
- 평가 confidence 부족
- 평가 기준 누락

동점은 `candidate_id` 오름차순으로 해소한다. LLM은 가중치, 감점, 정렬 또는 최종
순위를 결정하지 않는다.

## 4. 다양성 조정

활성화 시 이미 선택된 상위 후보와 같은 주 정책 분야, 국가·지역, 산출물 유형,
연구 시계가 반복될수록 설정값만큼 감점하는 결정적 greedy selection을 사용한다.
정책 분야와 국가는 근거 `TopicSignal`에서 가져오며 없는 값은 추측하지 않는다.

```text
adjusted_score =
  penalized_score
  - domain_repeat_count × domain_repeat_penalty
  - country_repeat_count × country_repeat_penalty
  - output_repeat_count × output_repeat_penalty
  - horizon_repeat_count × horizon_repeat_penalty
```

`enabled=false`이면 조정값은 0이고 감점 후 순위를 그대로 유지한다. 원점수,
일반 감점, 다양성 감점과 최종 점수를 모두 산출물에 보존한다.

## 5. 해석상 한계

현재 정규화 371건에는 실제 발행일·초록·키워드가 모두 없고 제목·저자는 파일명
파생값이다. 따라서 lexical 직접 중복은 의미 중복 판정이 아니며, 파일명 연도 기반
시의성은 연도 정밀도만 가진다. 전문가 골든셋이 없는 정확도·Recall 지표는 성능
수치로 추정하지 않고 `unavailable`로 보고한다.

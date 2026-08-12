# Golden retrieval dataset 작성 안내

이 문서는 `artifacts/human_review/golden_questions_template.csv` 를 채우는 방법을
설명한다. 스키마와 원칙은 [EVALUATION.md](./EVALUATION.md), 코퍼스 현황은
[IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) 를 따른다.

## 왜 필요한가

P2.6 retrieval benchmark 가 `Recall@5`, `Recall@10`, `MRR` 을 계산하려면 "이 질문에
대한 정답 문서와 페이지" 가 있어야 한다. 그 라벨이 golden dataset 이다.

**이것 없이는 lexical, vector, hybrid 중 무엇이 나은지 말할 수 없다.** 현재 세 검색이
모두 구현돼 있으나 품질 비교는 한 번도 하지 않았고, 계획의 운영 규칙이 그것을
금지한다 — *"품질 수치가 없으면 개선을 주장하지 않고 측정 계획부터 만든다."*

`EVALUATION.md` 5번 규칙이 이 작업을 사람에게 맡기는 이유도 명확하다 —
*"모델이 생성한 정답을 검토 없이 golden label 로 사용하지 않는다."*

## 준비된 것

| 파일 | 용도 |
|---|---|
| `golden_questions_template.csv` | 작성 양식. `gq-001`~`gq-040` 행이 미리 있다 |
| `golden_candidate_pool.csv` | 후보 문서 43건. 유형·연도 층화, 분량 많은 순 |

후보 풀은 색인 대상 331건에서 뽑았다. 후보 밖 문서를 써도 되지만, 층화 표본이라
유형이 한쪽으로 쏠리지 않는다.

| 유형 | 색인 대상 | 후보 |
|---|---|---|
| KIDA Brief | 136 | 17 |
| 국방논단 | 100 | 13 |
| 국방정책연구 | 59 | 8 |
| 연구보고서 | 36 | 5 |

## 컬럼별 작성법

| 컬럼 | 채우는 값 |
|---|---|
| `case_id` | 이미 채워져 있다. 바꾸지 않는다 |
| `question` | 실제 연구 질문. 아래 원칙 참조 |
| `topic` | 주제 분류. 자유롭게 정하되 일관되게 (예: `인지전`, `병역제도`, `획득정책`) |
| `difficulty` | `easy` / `medium` / `hard` |
| `query_slice` | 질의 유형. 아래 참조 |
| `relevant_publication_ids` | 정답 문서 ID. 여러 개면 `;` 로 구분 |
| `relevant_pages` | 근거 페이지. `12,15-17` 형식. 문서가 여럿이면 `;` 로 문서 순서와 맞춘다 |
| `relevance_grade` | 관련도 등급. 아래 참조 |
| `expected_evidence` | 그 페이지에서 어떤 내용이 근거가 되는지 한 줄 |
| `acceptable_abstention` | 근거 부족 시 "모른다"가 정답인가. `yes` / `no` |
| `created_by` | 작성자 |
| `reviewed_by` | **다른 사람**이 확인 후 기입 |
| `adjudication_status` | `draft` → `reviewed` → `agreed`. 불일치는 `disputed` |
| `notes` | 판단 근거, 애매한 점 |

### `query_slice` — 왜 나누는가

benchmark 는 전체 평균만 보면 안 된다. lexical 이 강한 질의와 vector 가 강한 질의가
다르므로 slice 별로 비교해야 어느 방식을 언제 쓸지 알 수 있다. 권장 값:

| 값 | 설명 | 예 |
|---|---|---|
| `exact_term` | 약어, 무기체계명, 정책 식별자 | `KAMD 요격 단계` |
| `concept` | 개념·이론 설명 요구 | `인지전 수행개념의 이론적 근거` |
| `comparative` | 비교·대조 | `미국과 한국의 확장억제 인식 차이` |
| `temporal` | 시점·변화 | `2020년 이후 병역제도 논의 변화` |
| `policy_impact` | 정책 함의·제언 | `인구감소가 상비병력에 주는 함의` |

`exact_term` 이 lexical baseline 의 강점 구간이다. 이 slice 를 충분히 넣지 않으면
vector 가 실제보다 좋아 보인다.

### `relevance_grade` — 등급을 쓰는 이유

`nDCG` 계산에 필요하고, "정답 1개" 보다 현실을 잘 반영한다.

| 등급 | 의미 |
|---|---|
| `3` | 질문에 직접 답하는 핵심 근거 |
| `2` | 관련 있고 유용하지만 부분적 |
| `1` | 주변적. 배경 정보 수준 |
| `0` | 관련 없음 |

문서가 여럿이면 `relevant_publication_ids` 순서에 맞춰 `;` 로 구분한다.

## 작성 예시

후보 풀의 실제 문서다.

```
문서: 사회심리학・군사이론 관점에서의 인지전 수행개념 연구
id  : pub:kida:abd8b50ffccb41148e28dbfd07ea8ed2
p1  : 국방정책연구 2024년 여름(40-2) 통권 제144호 pp. 77-120 …
p7  : … 인지전 수행개념 전략지침 이해 / 전략・작전환경 이해 / 문제규정 /
      작전적 접근 / 군사이론의 적용(작전구상) / 사회심리학 이론 적용 …
p8  : II. 이론적 고찰 1. 美 합참(JCS)의 작전구상 방법론
      (Operational Design Methodology) 가. 전략지침 이해 …
```

이 문서로 만들 수 있는 행:

```
case_id                  : gq-001
question                 : 인지전 수행개념을 구성할 때 미 합참의 작전구상
                           방법론은 어떤 단계로 적용되는가?
topic                    : 인지전
difficulty               : medium
query_slice              : concept
relevant_publication_ids : pub:kida:abd8b50ffccb41148e28dbfd07ea8ed2
relevant_pages           : 7-9
relevance_grade          : 3
expected_evidence        : 전략지침 이해·작전환경 이해·문제규정·작전적 접근으로
                           이어지는 작전구상 단계와 사회심리학 이론 적용 지점
acceptable_abstention    : no
created_by               : (작성자)
adjudication_status      : draft
```

## 작성 원칙

- **실제 연구 질문을 쓴다.** 문서를 보고 역으로 만든 "이 문서에 무엇이 쓰여 있나"
  질문은 검색 성능을 과대평가한다. *"내가 이 주제를 조사한다면 무엇을 물을까"* 로 쓴다.
- **정답 문서를 먼저 정하지 않는다.** 질문을 쓰고 나서 코퍼스에서 근거를 찾는 순서가
  실제 검색 상황과 같다.
- **한 질문에 여러 문서가 정답일 수 있다.** 오히려 그쪽이 현실적이다.
- **근거 없는 질문도 넣는다.** 코퍼스에 답이 없는 질문 몇 개는
  `acceptable_abstention=yes`, `relevant_publication_ids` 비움으로 넣는다.
  검색이 억지로 무언가를 반환하지 않는지 보는 데 쓴다.
- **페이지는 실제로 확인한 것만 적는다.** 추정으로 적으면 Recall 이 부정확해진다.
- **답을 모르겠으면 `notes` 에 적고 `adjudication_status=disputed` 로 둔다.**
  억지로 라벨을 붙이는 것보다 낫다.

## 검토 절차

`EVALUATION.md` 6절을 따른다.

1. 연구자가 질문과 예상 근거를 작성한다 → `adjudication_status=draft`
2. **다른 검토자가** publication 과 page label 을 독립 확인한다 →
   `reviewed_by` 기입, `adjudication_status=reviewed`
3. 불일치는 합의 또는 adjudication 으로 해결한다 → `agreed` 또는 `disputed`
4. corpus snapshot 과 dataset version 을 고정한다

2번이 형식이 아니다. 작성자 혼자 붙인 라벨은 그 사람의 검색 습관을 그대로 반영하고,
그 위에서 측정한 수치는 검색 품질이 아니라 라벨러 습관을 재는 것이 된다.

## 몇 개나 필요한가

계획은 30~50개를 제시한다. 양식은 40행이다.

slice 별로 최소 5개는 있어야 slice 별 비교가 의미를 갖는다. 5개 slice × 8개 = 40 이
기본 배분이지만, 실제 연구에서 자주 나오는 유형에 더 배분해도 된다.

**부분 작성도 유용하다.** 10개만 있어도 benchmark 를 돌려 파이프라인이 동작하는지
확인할 수 있다. 완성을 기다리지 말고 알려주면 중간 점검을 돌린다.

## 완료 후

알려주면 다음을 진행한다.

1. 형식 검증 — ID 존재 여부, 페이지 범위 유효성, 등급 값, 중복
2. slice·유형·등급 분포 리포트
3. P2.6 benchmark 구현
4. lexical / vector / hybrid 를 같은 dataset 으로 측정해 처음으로 품질 수치 산출

측정 결과의 해석과 어느 방식을 채택할지는 사람이 결정한다.

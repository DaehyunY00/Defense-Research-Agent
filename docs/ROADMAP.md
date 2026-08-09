# Defense Research Platform 로드맵

기준일: 2026-08-09

이 로드맵은 기능 수가 아니라 검증 가능한 연구 근거 계층을 우선한다. 상태는 실제 코드와
테스트를 기준으로 `implemented`, `partial`, `planned`, `missing`으로 표시한다.

## 플랫폼 방향

```text
Document Intelligence
↓
Retrieval
↓
Evaluation
↓
RAG
↓
Research Applications
```

Topic Discovery와 Research Lab은 폐기 대상이 아니라 이미 존재하는 상위 Application이다.
기반 계층이 강화되면 같은 provenance·retrieval·evaluation 계약을 공유한다.

## 우선순위와 현재 상태

| 우선순위 | 영역 | 상태 | 현재 근거 | 다음 완료 조건 |
|---|---|---|---|---|
| P0 | 기존 시스템 검증 | partial | 오프라인 테스트·mypy·ruff·health와 honest evaluation harness 존재 | 승인된 golden set과 반복 가능한 운영 smoke 기준 |
| P1 | Document Intelligence | partial | `ResearchPublication`, `PublicationPage`, page-aware `PublicationChunk`, deterministic chunker | parser/page adapter, PDF extraction, quality gate |
| P2 | Retrieval | partial | publication-level lexical ranking, filter, `find_similar` | chunk benchmark 후 BM25/dense/hybrid를 단계 검증 |
| P3 | Research Copilot | planned | 공통 agent/model/evidence 계약은 존재 | citation과 abstention을 갖춘 grounded answer path |
| P4 | Learning Companion | planned | 별도 application 없음 | 학습 목표·진도·근거 기반 설명 계약 |
| P5 | Model Runtime | partial | Fake/Anthropic structured gateway, 역할 route, timeout/retry 설정 | provider 운영 benchmark, 비용·latency·failure 관측성 |
| P6 | Product UI | partial | CLI, private API와 GCP 배포 계약 존재 | 연구자용 검색·근거검토·승인 UI |
| P7 | Advanced Intelligence | planned | 제한된 sandbox와 Research Lab 존재 | benchmark로 입증된 고급 routing/graph 기능만 채택 |

## P1 진행 상태

- [x] 정규화된 publication ID와 source checksum
- [x] page/chunk provenance domain contract
- [x] 같은 입력에서 같은 ID/checksum을 만드는 deterministic page chunker
- [ ] JSON page adapter와 parser abstraction
- [ ] PDF 본문 직접 extraction adapter
- [ ] 저추출·제어문자·중복·orphan quality gate
- [ ] page/chunk artifact writer와 provenance audit

현재 chunker는 ingestion이나 retrieval에 연결되지 않았다. 따라서 page citation, embedding,
vector search와 RAG가 구현됐다고 간주하지 않는다.

## 채택 원칙

1. Lexical retrieval은 국방정책 exact terminology의 baseline으로 유지한다.
2. Dense, hybrid, router와 reranker는 golden benchmark에서 baseline 대비 효과를 확인한 뒤
   유지하거나 제거한다.
3. 근거가 부족한 RAG application은 답을 추측하지 않고 abstain할 수 있어야 한다.
4. score, ranking, threshold, filtering, state transition과 approval은 결정적 Python 코드가
   담당한다.

## 다음 단일 작업

**Document Parser abstraction과 JSON page adapter**를 구현한다. 이 작업은 현재 원본
`page_texts`와 page-aware chunker 사이의 끊어진 provenance를 연결하고, 이후 PDF
extraction과 quality gate를 같은 interface 뒤에 추가할 수 있게 한다.

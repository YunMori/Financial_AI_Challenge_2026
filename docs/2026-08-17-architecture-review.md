# 아키텍처 진단 및 구현 현황 보고 — 2026-08-17

> 코드로부터 역으로 확인한 현재 상태다. 문서가 주장하는 것과 코드가 실제로
> 하는 것이 어디서 어긋나는지를 판정 대상으로 삼는다.

기준 커밋: `4b4fdce` (2026-08-13) · 작업 트리에 `eval/run_eval.py` 수정 1건,
`eval/progress.sh` 미추적 1건.

---

## 1. 전체 구조

모노레포. 네 축이 서로 다른 목적을 갖는다.

```
apps/api/     FastAPI · Python 3.12 · 앱 3,400 LOC + 테스트 1,400 LOC
apps/web/     Next.js 15 / React 19 App Router · 1,077 LOC
corpus/       수집→정제→청킹→색인 ETL + 원문 + 배경 통계
eval/         골든셋 160문항 + 채점 하니스 + 리포트 7종
docs/         기능명세서 · ADR 4종 · dev-log(1,166줄) · 사실검증 대장
```

### 설계의 축 — 판정은 전부 규칙, 생성만 LLM

`apps/api/app/pipeline.py` 가 ①~⑩ 전 단계를 조립한다. **HTTP 와 분리**돼 있는 것이
핵심이다 — 라우터(`routers/chat.py`)와 평가기(`eval/run_eval.py`)가 같은
`ChatPipeline.run()` 을 호출하므로 채점 결과가 라우터 변경에 흔들리지 않는다.

| 단계 | 방식 | 구현 위치 | 상태 |
|---|---|---|---|
| ① 입력 가드레일 | 정규식 | `guardrail/input_filter.py` | ✅ 인젝션 11패턴 **중화**(차단 아님) + PII 5종 마스킹 |
| ② 계층 C 선판정 | 정규식 | `tiering/rules.py` | ✅ ko 9 / en 5 / vi 6 패턴. LLM 호출 전 종료 |
| ③ 질의 정규화 | 사전 + 소형 LLM | `rag/normalize.py` | ✅ 3단(사전 역매핑 → 히트≥2면 LLM 생략 → 소형 모델) |
| ④ 하이브리드 검색 | 규칙 | `rag/retrieve.py` | ✅ dense ∥ BM25 → RRF(k=60) → 비자 필터 |
| ⑤ 리랭킹 | — | `rag/rerank.py` | ⛔ **미도입 확정**(ADR-001). 코드는 존치, 기본 off |
| ⑥ 컨텍스트 조립 | 규칙 | `rag/context.py` | ✅ `[근거 n]` 부여 + 숫자 집합 추출 + stale 판정 |
| ⑦ 생성 | LLM | `llm/anthropic_client.py` · `llm/local_client.py` | ✅ 백엔드 2종 + Null |
| ⑧⑨ 출력검사·계층확정 | 규칙 | `tiering/postprocess.py` | ✅ `finalize()` 순수 함수, 8단계 검사 |
| ⑩ AI 생성 표시 | UI | `components/common/Notices.tsx` | ✅ |

②와 ⑨가 안전성 주장을 지탱한다. 둘 다 프레임워크 밖 순수 함수라 단위 테스트와
골든셋 채점이 직접 호출할 수 있다 — **"프롬프트가 아니라 아키텍처로 강제한다"는
주장이 코드에서 실제로 참이다.**

### 경계(추상화)가 실제로 값을 했다

- `llm/base.py :: LLMClient`(Protocol) → Anthropic / Local / Null 3구현.
  ADR-004 의 로컬 전환에서 **파이프라인 본문을 한 줄도 고치지 않고** 백엔드를 교체했다.
- `rag/embed.py` — fastembed / sentence-transformers 전환 가능.
- `rag/rerank.py` — 껐지만 재측정 장치로 남겼다.
- `config.py :: Settings` — 모델명·임계값의 단일 출처. 색인기와 런타임이 같은 값을 본다.

### 상태 없음(stateless)

서버는 세션도 DB 도 갖지 않는다. `SessionContext` 는 클라이언트 `sessionStorage` 에
있고 매 `/chat` 요청 body 로 실려 온다. 회원·대화로그 테이블이 없다는 사실 자체가
개인정보 어필이자, 8주 일정에서 인증·DB 작업을 통째로 제거하는 장치다.

### 검색 상세

- **dense** — Chroma persistent + `intfloat/multilingual-e5-large`(fastembed ONNX).
  e5 접두어(`query:` / `passage:`) 처리 포함.
- **lexical** — `rank-bm25` + kiwipiepy. 도메인 복합명사를 고유명사로 등록하고,
  체류자격 코드(`E-9`/`E9`/`e-9` → `E9`)를 **형태소 분석 전에** 정규식으로 보호한다.
  임베딩만으로는 E-9 와 E-7 이 거의 같은 벡터가 되므로 BM25 가 변별의 유일한 수단이다.
- **융합** — RRF(순위만 합침, 척도 정규화 불필요) → 비자 메타 필터(후보 3건 미만이면
  해제) → 상위 5건.
- **게이트** — 언어별 top1 임계값 `ko 0.8246 / en 0.8445 / vi 0.8321`.
  margin 은 신뢰도 신호로 쓰지 않는다(0.0 = 비활성) — 코퍼스 밖 질의가 오히려 높은
  margin 을 냈기 때문.

### 출력 검사 순서 (`finalize()`)

싼 것부터 본다: 인용 존재 → 인용 실재성 → **숫자 대조** → 금지표현 → 민감정보 요구
→ 계층 강등(비-government 면 A→B, stale 이면 A→B) → 계층 B 문구 강제 삽입.

숫자 대조(`util/numerals.py`)가 가장 실용적인 환각 방어다. `1,000,000원` · `100만원` ·
`백만원` · `일백만원` 을 같은 값으로, `5만 달러` 와 `5만 원` 을 다른 값으로 취급하며
영어·베트남어 표기(`3.000.000 won`, `30 million won`)도 처리한다.

### SSE 계약

`meta` → `token`* → `citations` → (`invalidate`) → `done`.
`meta` 의 tier 는 **잠정값**이고 `done` 에서 확정된다. 스트리밍 중 출력 검사에
실패하면 `invalidate` 로 화면 텍스트를 폴백 카드로 교체한다 — 서버·클라이언트
양쪽에 구현돼 있다(`routers/chat.py:71-88`, `apps/web/app/[locale]/chat/page.tsx:65-69`).

---

## 2. 구현 현황 (실측)

### 기능별

| 기능 | 명세서 | 실제 |
|---|---|---|
| F1 다국어 온보딩 | ✅ | ✅ S1·S2 구현, 자유 입력 0개 |
| F2 계좌개설 내비게이터 | ⬜ | **데이터만.** `corpus/matrix/institutions.yaml` 은 있으나 이를 읽는 코드가 **0줄**, `visa_rules` 도 대부분 `unknown` |
| F3 서류 체크리스트 PDF | ⬜ | **선행 검증만.** weasyprint 핀 + Docker 폰트 + 조판 검증(`scripts/typeset/`) 통과. 생성 코드·라우트 없음 |
| F4 한도제한계좌 해제 가이드 | ⬜ | 전용 코드 없음 (F5 챗으로 답변은 가능) |
| F5 근거 기반 QA 챗봇 | ✅ | ✅ ①~⑩ 관통 |
| F6·F7·F8 | ⬜ | M1 범위 밖, 착수 없음 |

### 백엔드

- 테스트 **327 passed / 1 skipped / 1 xfailed** (13.5초).
- 엔드포인트 2개: `/healthz`, `POST /api/v1/chat`. CORS + slowapi 레이트리밋(분 20회).
- 인덱스 산출물 존재: `apps/api/data/chroma/`, `data/bm25.pkl`(88KB).

### 프론트엔드

구현된 화면은 **3개뿐** — S1 `/[locale]`, S2 `/[locale]/profile`, S4 `/[locale]/chat`.
S3 대시보드와 S5-1/2/3 은 파일 자체가 없다. 컴포넌트 3종(`CitationBadge`,
`TierNotice`, `Notices`), ko/en/vi 메시지 3파일 + `check-i18n` 빌드 게이트,
Accept-Language 를 q 값 순으로 존중하는 미들웨어.

### 코퍼스

22문서 / **71청크**. `publisher_type` 은 government 70 / fsi 1.
`manifest.json` 에 sha256·`verified_at`·`visa_scope`·라이선스까지 기록 — 근거가
바뀌면 드러난다. 통계 CSV 4종은 RAG 대상이 아닌 배경 근거다.

### 평가

골든셋 **160문항** (ko 77 / en 42 / vi 41 · account_opening 34 / limit_release 34 /
no_evidence 34 / tier_c_trap 34 / residence 24). 커밋된 리포트 7종.

| 실험 | 모드 | 인용 | Recall@5 | 폴백정확 | 과잉폴백 | 숫자 | 계층일치 | p50/p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| exp_002 | llm | 100 | 93.8 | 100 | 33.8★ | 100 | 74.4★ | 6,184 / 17,761★ |
| exp_003 | llm | 100 | 93.8 | 97.1 | 21.5★ | 100 | 75.0★ | 5,854 / 13,487★ |
| exp_005 | llm | 100 | 94.6 | 92.6 | 20.7★ | 100 | 70.5★ | 6,349 / 16,751★ |
| exp_007 | no-llm(ko) | — | 93.9 | 75.0 | 6.1 | — | — | 41 / 50 |

---

## 3. 판정 — 문서 주장과 실제의 격차

### 🔴 계측 결함 1건 (이번 진단에서 새로 발견)

**`eval/run_eval.py:107` 이 골든셋의 `visa` 를 파이프라인에 전달하지 못한다.**

```python
req = ChatRequest(message=row["question"], lang=row["lang"], visa=row.get("visa"))
```

`ChatRequest` 에 `visa` 필드는 없다 — 비자는 `context.visa` 에 있다. Pydantic 이
미지 필드를 **조용히 무시**하므로 에러 없이 통과하고 `req.context.visa` 는 항상
`None` 이 된다. 확인: `'visa' in ChatRequest.model_fields → False`.

골든셋 160문항 **전부**가 `visa` 값을 갖고 있는데, 그 결과:

- ④ 의 비자 메타데이터 필터가 채점에서 **한 번도 동작하지 않았다**
- ③ 의 검색어 비자 결합(`_with_visa`)은 질의문에 코드가 직접 적힌 경우에만 걸렸다

E-9/E-7 변별은 이 프로젝트가 BM25 를 쓰는 이유이자 핵심 주장인데, 그 경로가
정량 증거 없이 남아 있다. **커밋된 Recall@5 93~95% 는 비자 필터를 끈 상태의 값이다.**

### 🟠 런타임에 없는 안전장치 1건

**출력 언어 검사가 채점기에만 있고 런타임에는 없다.** `eval/metrics.py ::
detect_answer_language` 가 스크립트 비율로 언어 이탈을 판정하지만,
`postprocess.finalize()` 는 `lang` 을 폴백 문구와 계층 B 고지 삽입에만 쓴다.
즉 **베트남어 질문에 한국어로 답해도 인용·숫자·금지표현 검사를 전부 통과해
이용자에게 나간다.** metrics.py 의 주석이 이 사실을 스스로 지적하고 있으나
가드는 추가되지 않았다 — `citations` 생략과 같은 부류(모델의 선의에 기대던 자리)다.

### 🟠 미달 지표 3종 (문서도 인정)

1. **지연** — p50 6.2초 / p95 17.8초 vs 목표 P95 6초. dev-log 실측상 요청당 고정
   오버헤드 5~6초가 지배하며 `effort`·`thinking` 으로 줄지 않는다. TTFT 계측은
   추가됐으나 **목표를 총 완료 시간에서 TTFT 로 옮길지는 기획 판단 대기.**
2. **계층 일치율** — 70~75% vs 목표 90.
3. **과잉 폴백** — 20.7% vs 목표 8. 단 사유별로 보면 절반 이상이
   `unsupported_number`(= 환각을 정확히 막은 것)라 총량만 보면 반대 결론으로 간다.

### 🟠 측정이 멈춰 있다

- API 크레딧 소진으로 **exp_006 이후 llm 모드 채점 불가.** exp_006 은 측정은 됐으나
  리포트 파일이 유실(버그는 수정됨). 커밋된 정량 증거는 exp_001~005, 007 뿐이다.
- 로컬 전환(ADR-004)은 **배선과 정확성만 확인**됐고 성능 판정은 AWS GPU 대기.
  MPS 에서 골든셋 전량이 4~6시간이라 실행 불가(ko 1건 총 125초, TTFT 41.7초).
- 진행 중이던 로컬 채점(exp_008)의 산출물이 없다. `eval/progress.sh` 는 **다른
  세션의 scratchpad 경로**를 가리키고 있어 그대로는 동작하지 않는다.
- ADR-004 의 결정 게이트(모델 크기 확정, 하이브리드 여부)는 전부 미판정.

### 🟡 문서 드리프트

| 위치 | 적힌 값 | 실제 |
|---|---|---|
| README · functional-spec | 테스트 278개 | **327개** |
| functional-spec:303 | ko 0.839 / en 0.795 / vi 0.790 | **ko 0.8246 / en 0.8445 / vi 0.8321** |
| functional-spec:273,297 | 골든셋 160 / "골든셋 100문항" 혼재 | 160 으로 통일 필요 |
| `app/main.py` · `Dockerfile` · `config.py` | Render 전제 주석 | README 는 **Render 폐기·AWS 이전** 선언 |

### 🔵 사소한 것

- `rag/normalize.py:60-63` 의 `@lru_cache _cached()` 는 죽은 코드다. 항상 `None` 을
  반환하고 아무도 호출하지 않으며, 실제 캐시는 인스턴스 dict(`self._cache`)다.
- `apps/api/scripts/check_typeset.py` 와 `scripts/typeset/check_typeset.py` 가
  동일 128줄로 중복.
- 작업 트리 미커밋: `run_eval.py --resume` 기능 + `eval/progress.sh`(미추적).

---

## 4. 종합 판정

**F5(근거 기반 QA)는 프로덕션 수준의 골격을 갖췄다.** 안전성 주장이 프롬프트가
아니라 코드에 있다는 명제는 실제로 참이다 — ② 가 LLM 이전에 0ms 로 차단하고,
⑨ 가 모델 신고와 무관하게 계층을 확정하며, 둘 다 순수 함수로 테스트된다.
HTTP/파이프라인 분리, `LLMClient` Protocol, 임계값·모델명의 단일 출처 같은 경계
설계가 로컬 전환에서 실제로 값을 했다.

**남은 위험은 코드 품질이 아니라 측정과 배포다.**

- 정량 증거가 2026-08-12 에서 멈춰 있고, 그 이후의 두 큰 변경(계층 C 다국어 보강,
  로컬 백엔드)은 골든셋 전량으로 재확인되지 않았다.
- 비자 필터 경로는 애초에 한 번도 측정되지 않았다(위 🔴).
- 배포는 Render 폐기 후 AWS 이전 **선언만** 있고 실측·IaC 가 없다.
- 완성된 화면이 3개뿐이라 F2·F3·F4 는 백엔드 로직부터 착수해야 한다.

---

## 5. 권장 착수 순서

1. **`eval/run_eval.py:107` 의 visa 전달 수정** → `--no-llm --lang ko` 로 비자 필터
   유무의 Recall 차이를 먼저 잰다(비용 0). 이 한 줄이 지금까지의 검색 지표 해석을 바꾼다.
2. **`postprocess.finalize()` 에 출력 언어 검사 추가** — 폴백 사유 `language_mismatch`
   신설. 채점기의 `detect_answer_language` 를 그대로 재사용한다.
3. **문서 드리프트 4건 정정** (테스트 수, 임계값, Render 잔재).
4. **`--resume` + `progress.sh` 커밋** — 경로를 리포 상대경로로 바꿔서.
5. **AWS GPU 확보 후 exp_008**(로컬 전량 채점) → ADR-004 확정.

## 6. 검증 방법

```bash
cd apps/api && .venv/bin/python -m pytest -q          # 327 passed 기준선
python eval/run_eval.py --validate-only               # 골든셋 스키마
python eval/run_eval.py --no-llm --lang ko            # 비용 0 회귀 (visa 수정 전후 비교)
.venv/bin/python -m app.rag.cli --calibrate           # 임계값 분포
cd apps/web && npm run check-i18n                     # 다국어 키 누락
```

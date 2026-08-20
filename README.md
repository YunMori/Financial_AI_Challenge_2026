# K-Buddy — 체류자격 기반 외국인 금융정착 AI Agent

2026 금융 AI Challenge 출품작. 한국에 체류하는 외국인에게 **공개된 1차 출처에
근거해서만** 금융 절차를 안내하고, 근거가 없거나 개별 심사 영역이면
**답하지 않는 것을 설계로 강제**한다.

## 설계의 핵심

이 서비스의 안전성 주장은 프롬프트가 아니라 **코드**에 있다.

| 단계 | 구현 | 위치 |
|---|---|---|
| ② 계층 C 선판정 | 개별 승인·한도·금리 예측은 **LLM 호출 전에** 차단 | `app/tiering/rules.py` |
| ⑧⑨ 출력 검사 | 인용 존재·실재성 → **숫자 대조** → 금지표현 → 민감정보 요구 | `app/tiering/postprocess.py` |

**숫자 대조가 가장 실용적인 환각 방어다.** 답변의 모든 수치를 근거 텍스트와
대조하며, `1,000,000원` · `100만원` · `백만원` · `일백만원` 을 같은 값으로,
`5만 달러` 와 `5만 원` 을 다른 값으로 취급한다.

검색·판정·가드레일은 전부 로컬에서 실행되고, **외부로 나가는 것은 생성 호출
하나뿐**이다 (ADR-002).

## 빠른 시작

```bash
# 1) 백엔드
cd apps/api
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2) 환경변수 — ANTHROPIC_API_KEY 한 줄만 채우면 된다
cp .env.example .env && $EDITOR .env

# 3) 코퍼스 (수집 → 정제 → 청킹 → 색인)
python corpus/scripts/01_fetch.py      # 화이트리스트 도메인만 허용
python corpus/scripts/02_clean.py
python corpus/scripts/03_chunk.py
python corpus/scripts/04_index.py --rebuild

# 4) 서버
.venv/bin/uvicorn app.main:app --port 8000

# 5) 프론트
cd ../web && npm install && npm run dev
```

**키가 없어도 검색·계층 판정까지는 동작한다.** 생성만 폴백된다.

## 확인 방법

```bash
# 검색만 단독으로 (챗이 붙기 전 디버깅용)
cd apps/api && .venv/bin/python -m app.rag.cli "E-9 한도제한계좌 해제 서류"

# 검색 신뢰도 임계값 재보정 — 임베딩 모델이나 코퍼스를 바꾸면 반드시 실행
.venv/bin/python -m app.rag.cli --calibrate

# 테스트 (345개)
.venv/bin/python -m pytest -q

# 골든셋 자동 채점 (planner §13) — 리포트는 커밋한다
python eval/run_eval.py --validate-only          # 스키마만
python eval/run_eval.py --no-llm                 # 검색·판정만, 비용 0
python eval/run_eval.py --out eval/reports/exp_00N.json

# 다국어 키 누락 검사
cd apps/web && npm run check-i18n
```

## 배포

```bash
# 빌드 컨텍스트는 리포 루트다 (corpus/glossary 를 담아야 하므로)
docker build -f apps/api/Dockerfile -t kbuddy-api .

# API 백엔드 (외부 생성 호출)
docker run -p 10000:10000 -e ANTHROPIC_API_KEY=sk-ant-... kbuddy-api

# 로컬 백엔드 (외부 호출 없음, GPU 권장)
docker run -p 10000:10000 -e LLM_BACKEND=local kbuddy-api
```

- 백엔드: **AWS 서울 리전(ap-northeast-2) 이전 중** — GPU 인스턴스(g5/g6).
  로컬 생성 모델을 쓰면 planner §15.2 의 국내 리전 원칙이 **실제로 충족**된다
  (ADR-004). Render 배포는 폐기했다 — 배포 경로를 둘로 두면 8주 차에 사고가 난다.
- 프론트: Vercel (`apps/web/vercel.json`)

> ⚠ **AWS 이전은 아직 실측되지 않았다.** 검증되지 않은 IaC 를 지어내지 않고,
> 필요한 조건(리전·인스턴스·모델 가중치 사전 포함·비용 운영)을 ADR-004 에
> 적어 두었다. 메모리·지연 실측 후 배포 설정을 확정한다.

## 문서

| 파일 | 내용 |
|---|---|
| `planner.md` | 8주 실행 계획서 |
| `docs/functional-spec.md` | 기능명세서 (제출물) |
| `docs/fact-check.md` | 기획서 수치의 1차 출처 검증 대장 (5/11 닫힘) |
| `docs/spec-changes.md` | **기획서 수정 대상 28건** — 근거·조치 포함 |
| `eval/` | 골든셋 160문항 + 자동 채점 하니스 + 실험 리포트 |
| `docs/dev-log.md` | 실측값·결정·기획서와 어긋난 지점 |
| `docs/adr/` | 되돌리기 어려운 결정 기록 |
| `corpus/stats/SOURCES.md` | 통계 데이터 출처·해시 |

## 구조

```
apps/api/app/
├─ pipeline.py          ①~⑩ 조립 (HTTP 와 분리 — 평가기가 재사용)
├─ rag/                 정규화 · 토크나이저 · 검색 · 컨텍스트
├─ tiering/             계층 C 규칙 · 최종 확정 ★
├─ guardrail/           인젝션 중화 · PII 마스킹
├─ llm/                 Anthropic 클라이언트 · 프롬프트
├─ matrix/              요건 매트릭스 로더 · fit_score (F2, 생성 없음)
├─ docs/                체크리스트 조립 · WeasyPrint 렌더 (F3)
└─ util/numerals.py     숫자 정규화 (환각 방어의 축)

corpus/
├─ sources.yaml         수집 대상 (1차 출처만)
├─ scripts/             01_fetch → 02_clean → 03_chunk → 04_index
├─ matrix/              institutions.yaml (F2) · documents.yaml (F3)
├─ manifest.json        sha256 기록 — 근거가 바뀌면 드러난다
└─ stats/               배경 통계 (RAG 대상 아님)
```

## 필수기능 (F1~F5)

| | 엔드포인트 | 화면 | 성질 |
|---|---|---|---|
| F1 온보딩 | — | S1 `/[locale]` · S2 `/profile` | 규칙 (자유 입력 0) |
| F2 계좌개설 내비게이터 | `GET /institutions` | `/institutions` | **규칙만** — 생성 없음 |
| F3 서류 체크리스트 | `POST /checklist` (PDF) · `/checklist/preview` | `/tools/checklist` | 템플릿 |
| F4 한도해제 가이드 | `POST /guide/limit-release` | `/guide/limit-release` | RAG + LLM |
| F5 근거 기반 QA | `POST /chat` | S4 `/chat` | RAG + LLM |

F4 는 **F5 와 같은 `ChatPipeline`·같은 SSE 이벤트**를 탄다. 다른 것은 프로필로
한국어 질의를 조립한다는 것과, `done` 에 F3 로 잇는 `next_action` 이 실린다는
것뿐이다 (폴백일 때는 붙지 않는다).

**PDF 는 컨테이너에서만 렌더된다.** WeasyPrint 의 시스템 의존성(libpango 등)이
Windows·macOS 로컬에 없기 때문이며, 로컬에서 `POST /checklist` 가 503 을 내고
화면 내 HTML 체크리스트로 폴백하는 것이 정상이다. 조판 검증:

```bash
docker build -f apps/api/scripts/typeset/Dockerfile.checklist -t kb-checklist .
docker run --rm kb-checklist        # 글자별 폰트명 대조 (텍스트 비교로는 통과시키지 않는다)
```

## 알려진 제약

- 공개 언어는 **ko/en/vi 3종**. 원어민 검수를 확보한 만큼만 늘린다
  (검수되지 않은 언어는 공개하지 않는다는 원칙).
  **언어를 늘릴 때는 계층 C 규칙(`tiering/rules.py`)과 골든셋을 함께 늘려야
  한다.** 규칙이 한국어·영어뿐이던 동안 베트남어 함정이 계층 A 로 통과했다 —
  번역에 기대는 2차 방어는 의도를 보존하지 못한다.
- 검색 신뢰도 게이트가 책임지는 것은 **"근거 없음" 하나뿐**이다. 개별 심사
  요구(계층 C)는 주제상 관련이 있어 검색 점수가 높게 나오는 것이 정상이므로,
  게이트가 아니라 ②규칙·⑨계층 판정이 막는다 (ADR-001).
  임계값은 골든셋 100문항 실측: ko 0.8246 / vi 0.8321 / en 0.8445.
  **en·vi 는 무근거 표본이 2건·1건뿐이라 잠정값**이다.
- **리랭킹은 넣지 않는다** (ADR-001). `jina-reranker-v2` 가 Recall@5 를
  93.8% → 96.9% 로 올리지만 질의당 6.8초를 더 써 지연 예산을 두 배로 넘긴다.
- **지연 목표를 TTFT 로 재정의했다** (2026-08-20, `spec-changes.md` #27).
  로컬 4B 의 총 완료는 p50 21.8초 / p95 44.7초라 총 완료 P95 6초는 달성 불가능하다.
  공개 지표는 **TTFT p50 3.5초 / p95 18.4초**(exp_012)이고, 총 완료 시간은
  참고 지표로 계속 기록한다.
- 요건 매트릭스의 **비자별 셀은 전부 `unknown`** 이다. 은행별 계좌개설 요건의
  공식 근거를 확보하지 못했고, **추정으로 채우면 그 자체가 환각**이므로 비워 둔다.
  기관 단위(모바일 외국인등록증 수용)는 6곳 전부 `official` 이다 — 같은 보도자료의
  기관 열거에서 승격했다(`spec-changes.md` #28).
  `fit_score` 는 **미확인 항목을 0 점으로 접지 않는다.** 계산에서 빼고 `unverified`
  로 표시한다 — 확인 못 한 은행이 나쁜 은행으로 정렬되면 근거 없는 주장이 점수가 된다.
- **F2 의 LLM 설명 문장은 M1 에서 뺐다**(`spec-changes.md` #26). 조회 결과가 전부
  unknown 이라 생성할 내용이 없고, 카드마다 20~45초가 붙는다.

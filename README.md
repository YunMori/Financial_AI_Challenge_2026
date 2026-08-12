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

# 검색 신뢰도 임계값 재보정 — 임베딩 모델을 바꾸면 반드시 실행
.venv/bin/python -m app.rag.cli --calibrate

# 테스트 (263개)
.venv/bin/python -m pytest -q

# 다국어 키 누락 검사
cd apps/web && npm run check-i18n
```

## 배포

```bash
# 빌드 컨텍스트는 리포 루트다 (corpus/glossary 를 담아야 하므로)
docker build -f apps/api/Dockerfile -t kbuddy-api .
docker run -p 10000:10000 -e ANTHROPIC_API_KEY=sk-ant-... kbuddy-api
```

- 백엔드: Render (`render.yaml`) — **Standard(2GB) 이상 필수**. 실측 피크
  2,096 MiB, 스타터(512MB)로는 임베딩 모델이 올라가지 않는다 (ADR-003).
- 프론트: Vercel (`apps/web/vercel.json`)

## 문서

| 파일 | 내용 |
|---|---|
| `planner.md` | 8주 실행 계획서 |
| `docs/functional-spec.md` | 기능명세서 (제출물) |
| `docs/fact-check.md` | 기획서 수치의 1차 출처 검증 대장 (5/11 닫힘) |
| `docs/spec-changes.md` | **기획서 수정 대상 15건** — 근거·조치 포함 |
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
└─ util/numerals.py     숫자 정규화 (환각 방어의 축)

corpus/
├─ sources.yaml         수집 대상 (1차 출처만)
├─ scripts/             01_fetch → 02_clean → 03_chunk → 04_index
├─ manifest.json        sha256 기록 — 근거가 바뀌면 드러난다
└─ stats/               배경 통계 (RAG 대상 아님)
```

## 알려진 제약

- 공개 언어는 **ko/en/vi 3종**. 원어민 검수를 확보한 만큼만 늘린다
  (검수되지 않은 언어는 공개하지 않는다는 원칙).
- 검색 임계값 0.839 는 코퍼스 안/밖 각 8질의로 잡은 **잠정값**이다.
  분리 폭이 0.02 로 좁아 골든셋 확보 후 재보정이 필요하다.
- 요건 매트릭스는 대부분 `unknown` 이다. 은행별 계좌개설 요건의 공식 근거를
  확보하지 못했고, **추정으로 채우면 그 자체가 환각**이므로 비워 둔다.

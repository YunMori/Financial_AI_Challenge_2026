# 로컬 생성 노선 확정 및 모델 비교 계획 — 2026-08-17

- 상태: **계획 확정 / 미착수**
- 관련: ADR-004(로컬 생성) · ADR-002(LLM 선정) · ADR-003(임베딩·메모리) ·
  `docs/2026-08-17-architecture-review.md`(같은 날 아침 진단)
- 선행 문서: 이 계획의 §4 선행 조건 2건은 아침 진단 보고서 §3 의 🔴·🟠 항목이다

> 아침 진단에서 "ADR-004 의 결정 게이트가 AWS GPU 실측 없이는 판정 불가" 로
> 정리된 뒤, 그날 그 갈림길을 닫기로 하고 세운 실행 계획이다. ADR 은 실측이
> 끝난 뒤에 갱신한다 — 근거 없이 ADR 상태를 바꾸지 않는다는 ADR-004 의 원칙을
> 그대로 따른다.

---

## 1. 확정한 것

ADR-004 는 로컬 생성 전환의 **배선과 정확성만** 확인한 채 "잠정" 으로 멈춰 있고,
세 갈래(8B 승격 / 하이브리드 / API 복귀)를 미결로 남겼다. Qwen3-4B 실측에서
정규화 Recall@5 가 en 86.4% · vi 90.5% 로 목표(92%)에 미달한 것이 원인이다.

1. **노선** — 로컬 생성 + AWS 서울 리전 GPU. 하이브리드·API 복귀는 폴백으로만 남긴다.
   planner §15.2 의 `LLM_REGION=kr` 이 문구 교체가 아니라 실제로 충족된다.
2. **모델** — 단일 지정이 아니라 **후보 비교로 확정**한다(§2).
3. **Anthropic 경로** — 코드는 남기고 **기본값만 local 로** 전환. 실측 미달 시
   되돌릴 여지와 exp_002~005 리포트의 재현성을 함께 지킨다.

후보 조사 결과 **제약이 라이선스에서 VRAM 으로 옮겨갔다.** 유력 후보가 전부
Apache 2.0 · non-gated 라 Gemma 3 의 401 사고 원인이 사라졌고(Gemma 4 는 Apache 2.0),
이제 갈리는 것은 24GB 인스턴스에 무엇이 들어가느냐다.

---

## 2. 측정 세트

> ### 🔄 2026-08-17 갱신 — 로컬 검증 결과, `Qwen/Qwen3.5-4B` 로 확정
>
> 배선 검증(§7.7)을 실제로 돌린 결과 **`Qwen/Qwen3.5-4B` 만 이 장비에서 검증을
> 완료했고, 그 모델로 확정한다.** 6개 검사 전부 통과 —
> `AutoModelForCausalLM` 로드 OK, XGrammar 컴파일 1.4초, 문법 제약 생성에서
> `citations`·`numbers_used` 정상(ADR-004 ① 의 사고가 재현되지 않음).
>
> **8B 두 후보는 M3 Pro 18GB 에서 검증하지 못했다.** 실패 형태가 두 번 달랐다:
>
> | 시도 | 결과 |
> |---|---|
> | `.to(device)` | 가중치 로드 63초 성공 → **직후 OOM 킬**(peak 2× = ~32GB) |
> | `device_map` (수정 후) | **SIGBUS(138)** — 스왑 6GB 중 980MB 만 남아 mmap 실패 |
>
> `device_map` 수정 자체는 유효하고 **AWS 에서 더 중요하다** — 24GB GPU 에
> Qwen3.5-9B(~19GB)를 `.to()` 로 올리면 순간 38GB 가 필요해 똑같이 죽는다.
> 로컬에서 먼저 만난 것이 이득이었다.
>
> → 아래 1차 3종 중 **Gemma-4-E4B-it · Sailor2-8B-Chat 은 첫 검증을 AWS 에서**
>   한다. 로컬 스크리닝(§7.9)은 Qwen3.5-4B 단독으로 간다.

### 2.1 1차 — 필수 3종. 서로 다른 가설을 하나씩 검증한다

| 후보 | VRAM(bf16) | 검증하는 가설 |
|---|---:|---|
| **Qwen3.5-4B** (5B params) | ~10GB | **계열 연속성.** Qwen3-4B 대비 순증분을 잰다. 201개 언어 공식 지원이 SEA 특화 SFT 없이도 vi 를 충족하는가 |
| **Gemma-4-E4B-it** (8B, 유효 4.5B) | ~16GB | **MatFormer 효율.** 유효 4.5B 가 실제 8B 메모리를 쓰면서 품질을 내는가. 140+ 언어 사전학습의 다국어 폭 |
| **Sailor2-8B-Chat** | ~17GB | **SEA 특화 SFT 의 값어치.** vi 포함 15개 언어 명시 학습이 구세대 백본(Qwen2.5) 열세를 뒤집는가 |

세 후보의 대립이 이 비교의 핵심이다 — **신형 백본의 광범위 다국어(Qwen3.5) vs
구세대 백본의 SEA 집중 학습(Sailor2).** 우리 최약 고리가 vi 정규화이므로 여기서
갈린다. Gemma-4-E4B 는 제3의 축(유효 파라미터 대비 품질)이다.

### 2.2 2차 — 1차 결과에 따라 조건부

| 후보 | VRAM | 조건 |
|---|---:|---|
| **Qwen-SEA-LION-v4-4B-VL** | ~8GB | **Sailor2 가 이겼을 때만.** SEA SFT 가 유효하다는 뜻이므로, 같은 SFT 를 신형 백본에 얹은 이 후보가 다음 수다. ⚠ §2.4 |
| **Qwen3.5-9B** | ~19GB | 1차 전원 미달 시 승격 경로 |

### 2.3 제외 — `Gemma-4-E2B-it`

VRAM 10GB 로 Qwen3.5-4B 와 같은데 품질이 확연히 낮다(MMLU-Pro 60.0). 우리 작업은
지시 준수 의존도가 높다 — `numbers_used` 완전 나열, `citations` 채우기, 3~5문장
제약, 요청 언어 유지. 하나라도 무너지면 후처리가 정상 답변을 차단한다.
**같은 메모리에 우월한 대안이 있으므로 지배당한 선택지다.**

### 2.4 ⚠ `Qwen-SEA-LION-v4-4B-VL` 은 코드 수정이 필요할 수 있다

VL(vision-language) 모델이라 텍스트 전용 파이프라인에 그대로 꽂히지 않는다.

- `local_client.py:183` 이 `AutoModelForCausalLM.from_pretrained` 를 쓴다. VL 은
  보통 `AutoModelForImageTextToText` 계열이라 **로드 자체가 실패**할 수 있다
- `local_client.py:198` 의 `vocab_size=self._model.config.vocab_size` — VL 은 이 값이
  `config.text_config.vocab_size` 아래 있는 경우가 많다. 틀리면 XGrammar
  `TokenizerInfo` 가 어긋나 **문법 제약이 조용히 깨진다** (ADR-004 의 `citations`
  생략 사고와 같은 형태다)
- 비전 타워가 VRAM 을 점유하는데 우리는 이미지를 보내지 않는다 — 순수 낭비

2차로 미룬 이유가 이것이다. 라이선스도 "확인 필요" 로 남아 있다.

---

## 3. 메모리 예산

**e5-large 는 비교 기간 동안 GPU 에 올리지 않는다.** 현재 `rag/embed.py` 는
fastembed(ONNX, CPU) 경로이고, `EMBED_BACKEND=sentence_transformers` 로 GPU 에
올리면 임베딩 수치가 미세하게 달라져 **보정된 임계값이 흔들린다.** 모델 비교
중에 검색까지 함께 바꾸면 무엇이 원인인지 가릴 수 없다.

| 후보 | 점유 | g5/g6.xlarge (24GB) | M3 Pro 18GB | M3 Pro 36GB |
|---|---:|---|---|---|
| Qwen3.5-4B + KV | ~11GB | 여유 | 빠듯하나 가능 | 여유 |
| Gemma-4-E4B-it + KV | ~17GB | 여유 | **불가** | 가능 |
| Sailor2-8B-Chat + KV | ~18GB | 가능 | **불가** | 가능 |
| Qwen3.5-9B + KV | ~21GB | 빠듯하나 가능 | **불가** | 빠듯 |
| 위 + e5-large GPU 동거 | +~1GB | 9B 에서만 위험 | — | — |

KV 캐시는 근거 5건 + 시스템 프롬프트 ≈ 6K 토큰, 출력 2048 기준 1~1.5GB 로 잡았다.
통합 메모리는 OS·기타 프로세스 몫을 빼야 하므로 표기값보다 여유가 적다.

→ **e5-large 를 CPU 에 두면 24GB 한 장으로 2차 후보까지 전부 덮인다.**
   e5 의 GPU 이전은 모델 확정 **후** 별건으로 다룬다(ADR-003 미해결 항목).

---

## 4. ★ 선행 조건 — 측정 전에 반드시 고쳐야 하는 2건

지금 상태로 후보를 재면 **틀린 것을 세 벌 재게 된다.**

### 4.1 `eval/run_eval.py:107` — 골든셋의 `visa` 가 파이프라인에 도달하지 않는다

```python
req = ChatRequest(message=row["question"], lang=row["lang"], visa=row.get("visa"))
```

`ChatRequest` 에 `visa` 필드가 없다(비자는 `context.visa`). Pydantic 이 미지 필드를
조용히 무시해 `req.context.visa` 는 항상 `None` 이다.

```python
req = ChatRequest(
    message=row["question"], lang=row["lang"],
    context=SessionContext(visa=row.get("visa")),
)
```

골든셋 160문항 전부가 `visa` 를 갖고 있으므로, 고치지 않으면 ④ 의 비자 메타 필터
(`rag/retrieve.py:203-211`)와 ③ 의 검색어 비자 결합(`normalize.py :: _with_visa`)이
모든 후보에서 꺼진 채 측정된다.

### 4.2 출력 언어 검사가 런타임에 없다 — `tiering/postprocess.py`

`eval/metrics.py :: detect_answer_language` 는 있으나 `finalize()` 는 `lang` 을
폴백 문구와 계층 B 고지에만 쓴다. **요청 언어가 아닌 언어로 답해도 인용·숫자·
금지표현 검사를 전부 통과해 이용자에게 나간다.**

이번 비교의 핵심 축이 다국어 품질인데, 채점기만 보고 제품은 안 막는 상태로
판정하게 된다.

- `FallbackReason.LANGUAGE_MISMATCH` 를 `schemas/common.py` 에 추가
- `finalize()` 의 5번(민감정보 요구)과 6번(계층 강등) 사이에 삽입 — 형식 검사를
  통과한 뒤, 계층을 매기기 전이 맞는 자리다
- 판정 함수를 `eval/metrics.py` → `app/util/` 로 옮겨 **채점기와 런타임이 같은
  함수를 쓰게** 한다. 두 벌이면 언젠가 어긋난다
- `i18n/fallbacks.py` 에 3언어 문구 추가

---

## 5. ★★ 비교 방법 — 무엇이 모델 간 직접 비교 가능한가

임계값(ko 0.8246 / en 0.8445 / vi 0.8321)은 **haiku 번역이 켜진 상태**로 보정된
값이다. 정규화 모델이 바뀌면 검색어가 바뀌고 점수 분포가 이동한다.
**후보마다 재보정하지 않으면 게이트 의존 지표는 모델 품질이 아니라 임계값
부적합을 재게 된다.**

| 지표 | 임계값 의존 | 비교 |
|---|---|---|
| Recall@5 (폴백 문항 포함) | ✗ | **직접 비교 가능** |
| 인용률 · 숫자 정확도 · 언어 일치 · 금지표현 | ✗ | **직접 비교 가능** |
| 과잉폴백 · 폴백정확 · 폴백사유일치 · 계층일치 | **✓** | **후보별 재보정 후에만** |

재보정에 추가 실행은 필요 없다 — `Outcome.top1` 이 **폴백된 문항까지 포함해 모든
문항에 기록**되기 때문이다(하니스가 이미 그렇게 고쳐져 있다).

> ⚠ **정정.** 처음에는 "리포트 JSON 하나에 재보정 입력이 다 들어 있다"고 적었으나
> 그렇지 않다. `score()` 는 문항별 `top1` 을 내보내지 않고, 리포트의 `failures`
> 에는 **실패 문항의 top1 만** 들어 있다. 재보정 입력은 `--partial` JSONL 이다.
> 채점을 돌릴 때 `--partial` 을 반드시 함께 준다.

→ **`eval/recalibrate.py` 신설** (구현 완료). `--partial` JSONL 을 받아 정상 문항 vs
   `no_evidence` 문항의 `top1` 분포에서 언어별 임계값을 재산출한다.

- **`upstream_error` 는 표본에서 뺀다** — 판단이 아니라 미실행이다. 세면
  `--no-llm` 파셜에서 정상 문항이 전부 "잘못 막힌 것"으로 잡힌다.
- 계층 C 함정은 표본에서 제외한다. 게이트가 책임지는 것은 "근거 없음" 하나뿐이다.
- **분리 폭이 음수면 값을 추천하지 않는다.** 억지로 고른 값을 `.env` 형태로
  내놓는 것 자체가 위험하다 — 고칠 대상은 임계값이 아니라 검색·정규화다.
- 여러 모델의 결과가 섞이면 **거부한다**(§6.1).

---

## 6. ★ 장치 전략 — MPS 는 스크리닝, CUDA 는 판정

ADR-004 가 MPS 를 배제한 논거는 "틀리다" 가 아니라 **"느리다"**(160문항 4~6시간)
였고, 실제로 그 문서의 정규화 개선(vi 81.0 → 90.5%)은 MPS 에서 측정한 값이다.
MPS 를 못 쓰는 것이 아니라, **어디까지 쓸 수 있는지를 나눠야 한다.**

### 6.1 dtype 이 장치마다 다르다 ★

`local_client.py :: resolve_dtype`

```python
if device == "cuda": return torch.bfloat16
if device == "mps":  return torch.float16
```

**MPS 는 fp16, CUDA 는 bf16 으로 돈다.** 두 포맷은 지수·가수 배분이 달라서,
`do_sample=False`(그리디)여도 로짓이 근소하게 갈리면 토큰 선택이 뒤집히고,
한 토큰이 갈리면 이후 생성 전체가 발산한다.

**따라서 MPS 수치를 CUDA 수치와 같은 표에 놓을 수 없다.** 이것은
`run_eval.backend_info()` 가 "로컬 모델 수치를 API 기준선과 같은 표에 섞으면 안
된다" 며 device 를 리포트에 남기게 한 것과 **같은 종류의 문제**다 — 그 계측이 왜
device 까지 기록하는지가 여기서 드러난다.

`LOCAL_DTYPE=bfloat16` 으로 강제하는 탈출구는 있으나, `config.py:85` 주석이
"mps 는 bfloat16 지원이 고르지 않다" 고 적어 둔 판단이라 쓰려면 먼저 확인해야 한다.

### 6.2 Gemma 를 fp16 으로 재면 dtype 아티팩트를 잰다

Gemma 계열은 bf16 학습이고 fp16 추론에서 오버플로가 보고돼 온 계열이다. MPS 에서
`Gemma-4-E4B-it` 을 재면 **모델 품질이 아니라 정밀도 문제**를 측정할 위험이 있다.
비교의 한 축이 통째로 오염되므로 **Gemma 는 MPS 측정 대상에서 뺀다.**

### 6.3 8B 후보는 통합 메모리에 안 올라갈 수 있다

§3 표 참조. M3 Pro 18GB 에서 여유가 있는 것은 `Qwen3.5-4B` 하나뿐이다.

### 6.4 그래서 이렇게 나눈다

| 목적 | 장치 | 근거 |
|---|---|---|
| 선행 조건 4.1·4.2 회귀 | **장치 무관** | visa 는 `--no-llm` 으로 문항당 40ms·비용 0, 언어 검사는 단위 테스트 |
| **후보 배선 스모크** | CPU 또는 MPS | 로드·chat template·XGrammar 컴파일. §7.7 |
| **파이프라인 스크리닝** | **MPS** (Qwen3.5-4B 한정) | 선행 수정이 실제로 걸렸는지, 프롬프트 회귀 |
| **게이트 판정** | **CUDA 필수** | dtype·메모리·지연 전부 |

---

## 7. 작업 순서

```
── 장치 무관 (지금 바로) ───────────────────────────────
 1. 후보 리포 경로·라이선스 확인       SEA-LION 라이선스, 정확한 org prefix
 2. 선행 조건 4.1 (visa)               --no-llm --lang ko, 비용 0
 3. 선행 조건 4.2 (언어 검사)          단위 테스트 포함
 4. eval/recalibrate.py                비교 성립의 전제
 5. config 기본값 + ADR 3종
 6. --resume · progress.sh 커밋        2시간×3회 실행에 필수

── 로컬 (GPU 켜기 전) ─────────────────────────────────
 7. scripts/verify_local_model.py 신설 → 후보 3종 배선 스모크
 8. eval/report_charts.py 신설 → 튜닝 과정 보고서 (기존 데이터로 즉시)
 9. MPS 스크리닝 — Qwen3.5-4B, vi 41문항

── CUDA (AWS) ─────────────────────────────────────────
10. 후보 3종 전량 채점 → ADR-004 확정
11. 후보 비교 보고서 생성 (8번 도구 재사용)
12. 문서 드리프트 정리
```

**2~4 는 GPU 없이 지금 할 수 있고, 하지 않으면 9 의 결과를 신뢰할 수 없다.**

### 7.5 설정 기본값 — `apps/api/app/config.py`

| 항목 | 현재 | 변경 |
|---|---|---|
| `llm_backend` | `"anthropic"` | **`"local"`** |
| `local_model` | `"google/gemma-3-4b-it"` (401 나던 값) | 1차 1순위 (Qwen3.5-4B) |

주석을 측정 세트와 선정 이유로 갱신한다. 지금은 "한국어 특화 모델은 ko/en 전용이라
탈락" 까지만 있어 왜 이 후보들인지가 없다.

### 7.6 ADR 갱신

- **ADR-004** — 상태 `잠정` → **`노선 확정 / 모델 실측 대기`**. 측정 세트·메모리
  예산·**장치 전략**·선정 게이트·제외 근거(E2B 지배당함, VL 코드 리스크)를 명시.
  하이브리드·API 복귀를 **폴백으로 강등**
- **ADR-002** — "현재 프로덕션 경로는 여전히 Anthropic 이다"(66행)가 기본값 변경으로
  거짓이 된다. 상태에 "대체 진행 중(ADR-004)" 표기
- **ADR-003** — 메모리 표를 AWS 기준으로 재작성. e5-large 의 GPU 이전은 임계값
  재보정을 동반하므로 모델 확정 후 별건임을 명시

### 7.7 `scripts/verify_local_model.py` 신설 ★

**GPU 시간을 사기 전에 배선 리스크를 턴다.** 모델마다 chat template 이 다르고,
XGrammar `TokenizerInfo` 가 어긋날 수 있고, `apply_chat_template(..., return_dict=True)`
가 안 되는 모델도 있다. 이걸 AWS 에서 처음 마주치면 **인스턴스 요금을 태우면서
디버깅**하게 된다. `Qwen3.5-4B` 는 아직 이 파이프라인에 물려 본 적이 없는 모델이다.

`scripts/verify_generation.py` 가 Anthropic 경로에 대해 하는 일의 로컬 대응물이다.

| 검사 | 실패 시 |
|---|---|
| 모델 로드 (`AutoModelForCausalLM`) | VL·MoE 계열은 다른 클래스가 필요 |
| `apply_chat_template(return_dict=True)` | attention_mask 누락 경고 → 생성 품질 저하 |
| `TokenizerInfo.from_huggingface(vocab_size=…)` | 문법 제약이 조용히 깨짐 |
| 문법 제약 하 짧은 생성 1건 | `LLMAnswer` 6필드가 다 채워지는가 |
| `_require_all_fields` 효과 | `citations` 가 생략되지 않는가 |

`max_new_tokens` 를 작게 잡아 후보당 수 분에 끝낸다.

### 7.8 `eval/report_charts.py` 신설 — Plotly 보고서 ★

리포트 JSON 은 튜닝의 정량 증거이지만 **사람이 읽는 형태가 아니다.** planner §13 은
이 파일들을 "발표 자료의 튜닝 과정 슬라이드 원자료 — 개인 참가에서 정량 근거를
보여줄 수 있는 거의 유일한 수단" 으로 못박아 두었다. 기획서·발표에 쓰려면 그림이
필요하다.

**두 모드를 한 도구에 둔다.** 후보 비교는 GPU 실측 후에나 가능하지만, 튜닝 과정은
`exp_001`~`exp_007` 로 **지금 바로** 만들 수 있다.

```bash
python eval/report_charts.py --history                  # exp_001~007 튜닝 과정
python eval/report_charts.py --compare eval/reports/exp_008_*_cuda.json
```

| 그림 | 무엇을 보여주는가 |
|---|---|
| 튜닝 과정 시계열 | 실험별 인용률·Recall@5·과잉폴백·폴백정확 추이. **무엇을 고쳐서 무엇이 나아졌나** |
| 언어별 Recall@5 | ko/en/vi × 후보. **vi 가설이 여기서 갈린다** |
| 과잉폴백 사유 분해 | `low_confidence`(고칠 대상) vs `unsupported_number`(환각을 막은 것). 총량만 보면 반대 결론으로 간다 |
| 신뢰도 분리 산점도 | 정상 문항 vs `no_evidence` 의 `top1` 분포. **게이트가 실제로 분리하는지를 눈으로 증명**한다 — 기획서에서 가장 값이 큰 그림 |
| 지연 p50/p95 · TTFT | 후보별. 목표선 함께 표시 |

- 출력: **자기완결 HTML**(plotly.js 인라인) + PNG. 기획서에 붙일 수 있어야 한다
- `requirements.txt` 에 `plotly` · `kaleido`(PNG 내보내기) 추가
- **device 가 섞인 입력은 거부한다**(§6.1·§7.10) — 그림은 표보다 더 쉽게 오해를 만든다

### 7.9 MPS 스크리닝 — `Qwen3.5-4B`, vi 41문항

목적은 **합격 판정이 아니라 선행 수정이 실제로 걸렸는지 확인**이다.
vi 를 고르는 이유는 최약 고리이자 언어 검사가 가장 잘 드러나는 축이기 때문이다.
ADR-004 실측(vi 42.2초/건) 기준 41문항이면 **30분~1시간**이다.

확인 항목:

- `context.visa` 가 검색에 실제로 반영되는가 (`retrieved_chunk_ids` 변화)
- 언어 이탈이 `LANGUAGE_MISMATCH` 로 잡히는가 — **실제 사례가 나오는지**
- 문법 제약 하에 `citations`·`numbers_used` 가 채워지는가
- 답변이 400자 제약을 지키는가 (길이 초과 → 뒤 필드 잘림 사고 재발 여부)

### 7.10 리포트 명명 — device 를 파일명에 넣는다

`backend_info()` 가 리포트 **안에** device 를 남기지만, 파일명으로도 갈라야 한다.
`run_eval.py:139-144` 주석이 경고한 "나중에 파일명만 보고 비교하게 되는" 사고를
막기 위해서다.

```
eval/reports/exp_008_{model}_{device}.json     예: exp_008_qwen35-4b_mps.json
                                                   exp_008_qwen35-4b_cuda.json
```

`eval/recalibrate.py` 는 입력 리포트의 device 가 섞이면 **거부**한다.

### 7.11 AWS 실측

g6.xlarge(L4 24GB) 또는 g5.xlarge(A10G 24GB), ap-northeast-2. 후보별 골든셋
160문항 전량. MPS 4~6시간이 GPU 에서 얼마로 줄어드는지가 지연 목표 재정의의
입력이다. 시간당 단가는 실행 전 확인.

---

## 8. 선정 게이트

**판정은 CUDA bf16 리포트로만 한다.** MPS 리포트는 스크리닝 기록이며 게이트에
넣지 않는다(§6.1).

| 지표 | 기준 | 출처 |
|---|---|---|
| 근거 인용률 | 100% | ADR-004 |
| 숫자 정확도 | ≥98% | ADR-004 |
| 금지표현 | 0건 | ADR-004 |
| `numbers_used` 완전성 | `gold_numbers` 대조 | ADR-004 |
| 정규화 Recall@5 (en·vi) | ≥92% | 4B 미달분(86.4 / 90.5) |
| **출력 언어 일치** | **≥95%** | 신규 (§4.2) |
| 지연 | TTFT 로 재정의 | planner §14.2 |

1차 3종 전원 미달이면 Qwen3.5-9B 승격 → 그래도 미달이면 **정규화만 API(haiku)** 로
되돌리는 하이브리드. 그 경우 "외부로 나가는 것이 없다" 주장은 성립하지 않고
ADR-002 의 원래 서술("외부로 나가는 것은 생성 호출 하나뿐")로 돌아간다는 것을
ADR-004 에 함께 적는다.

---

## 9. 검증

```bash
# ── 장치 무관 ────────────────────────────────────────────
# 선행 조건 4.1 — Recall@5 가 움직여야 정상이다 (비용 0)
python eval/run_eval.py --no-llm --lang ko --out /tmp/before.json
python eval/run_eval.py --no-llm --lang ko --out /tmp/after.json

# 선행 조건 4.2 — vi 질의에 한국어 답변을 넣은 finalize() 단위 테스트가
#                 LANGUAGE_MISMATCH 를 내야 한다
cd apps/api && .venv/bin/python -m pytest -q          # 327 passed 기준선 + 신규

# ── 로컬 (GPU 전) ────────────────────────────────────────
.venv/bin/python scripts/verify_local_model.py --model <후보>   # 배선 스모크

LLM_BACKEND=local LOCAL_MODEL=Qwen/Qwen3.5-4B \
python eval/run_eval.py --lang vi \
    --partial eval/reports/exp_008_qwen35-4b_mps_partial.jsonl \
    --out     eval/reports/exp_008_qwen35-4b_mps.json
./eval/progress.sh -w

# ── CUDA (AWS) ───────────────────────────────────────────
LLM_BACKEND=local LOCAL_MODEL=<후보> LOCAL_DEVICE=cuda \
python eval/run_eval.py \
    --partial eval/reports/exp_008_<후보>_cuda_partial.jsonl \
    --out     eval/reports/exp_008_<후보>_cuda.json

python eval/recalibrate.py eval/reports/exp_008_*_cuda.json   # 같은 자에 놓기

curl -s localhost:8000/healthz | jq      # llm_configured / index_present
cd apps/web && npm run check-i18n
```

리포트는 커밋한다 — 모델 선정의 유일한 정량 증거다.

---

## 10. 🔄 2026-08-18 — 확정 모델의 슬림화

> 상세: **[로컬 생성 모델 슬림화](./2026-08-18-model-slimming.md)**

확정한 `Qwen/Qwen3.5-4B` 가 `Qwen3_5ForConditionalGeneration` 이라 **비전 타워
0.667GB(7.2%) + MTP 헤드 0.241GB(2.6%)를 쓰지도 않으면서 싣고 있었다.**
§2.4 가 SEA-LION VL 후보를 두고 지적한 "순수 낭비"가 같은 자리에 있었던 셈이다.

텍스트 스택만 추출해 `Qwen3_5ForCausalLM` 로 다시 쓰고(무손실), torchao int8
weight-only 를 얹는다. 새 의존성은 0 이다 — `torchao` 가 torch 와 함께 이미 들어와
있어 §7.8 의 plotly 처럼 requirements 를 늘리지 않는다.

실측 (MPS · M3 Pro 18GB):

| 산출물 | 디스크 | 로드 | ③ 정규화 | ⑦ 생성 | 결정 |
|---|---:|---:|---:|---:|---|
| 원본 (fp16) | 9.32 GB | 28초 | 2.4초 | 194.8초 | 기준선 |
| `models/qwen35-4b-textonly` | **8.43 GB** | **17초** | 2.4초 | 197.5초 | **채택** |
| `models/qwen35-4b-textonly-int8` | 4.87 GB | 15초 | **20.8초** | 측정 중단 | **AWS 로 이월** |

**① textonly 는 무손실이다** — 로짓 최대차 `0.000e+00`, ③ 검색어와 ⑦ 답변(151자)이
글자 단위로 동일. 그래서 **vi 41문항을 돌리지 않는다**: 같은 출력을 내는 모델로
exp_008 대조군을 재생산할 뿐이다.

**② int8 은 MPS 에서 8.7배 느려 판정할 수 없다.** 크기 목표(−42%)와 배선 검사
6종은 전부 통과했으므로 **탈락이 아니라 이월**이다 — §2 가 8B 두 후보를 두고
"첫 검증을 AWS 에서 한다"고 한 것과 같은 형태다. MPS 에 int8 행렬곱 커널이 없어
매 연산마다 역양자화하는 것이 원인으로 보이고, CUDA 는 텐서코어가 있어 뒤집힐 수
있다. 리포트는 `exp_009_qwen35-4b-textonly-int8_cuda.json` 으로 §7.10 을 따른다.

**③ 계획서의 전제 하나가 틀렸다** — 비전 타워 제거의 **런타임 메모리 이득은 0
이다.** `AutoModelForCausalLM` 이 원본에서도 텍스트 스택만 올리고 있었다
(양쪽 다 4841.5M params · `visual=False`). 남는 이득은 디스크·콜드스타트·전송량이다.

`config.py` 의 `local_model` 기본값은 **바꾸지 않는다.** 품질 근거는 충분하지만,
`models/…` 는 새 체크아웃에 없어서 기본값으로 두면 `64dc422` 가 고친 "기본값이
gated repo 라 `LLM_BACKEND=local` 이 죽어 있던" 사고와 같은 형태가 된다.
옵트인(`LOCAL_MODEL=models/qwen35-4b-textonly`)으로 쓰고, 배포 경로가 정해질 때
함께 다룬다.

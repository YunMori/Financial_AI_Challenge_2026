# 로컬 생성 모델 슬림화 — 텍스트 전용 추출 + int8 양자화 · 2026-08-18

- 상태: **① textonly 채택 · ② int8 은 AWS 로 이월** (2026-08-18 결정)
- 관련: [2026-08-17 로컬 생성 노선 확정 및 모델 비교 계획](./2026-08-17-local-llm-plan.md) ·
  ADR-004(로컬 생성) · ADR-003(임베딩·메모리)
- 산출물: `models/qwen35-4b-textonly` · `models/qwen35-4b-textonly-int8` (둘 다 gitignore)
- 도구: `apps/api/scripts/export_local_model.py` · `apps/api/scripts/check_equivalence.py`

> `config.py:100` 이 `Qwen/Qwen3.5-4B` 로 확정된 뒤, 그 모델이 우리 파이프라인에
> **필요 없는 부분을 끌고 다닌다**는 것을 실측으로 확인하고 잘라 낸 기록이다.

---

## 1. 왜 줄이는가

### 1.1 실측 분해 — 9.8% 는 한 번도 쓰이지 않는다

`Qwen/Qwen3.5-4B` 는 이름과 달리 `Qwen3_5ForConditionalGeneration` 이다.
safetensors 헤더를 직접 읽어 그룹별로 합산했다:

| 그룹 | 크기 | 파라미터 | 비중 | 우리 경로에서 |
|---|---:|---:|---:|---|
| TEXT_LM | 7.140 GB | 3570.1M | 76.6% | 쓴다 |
| EMBED (lm_head 와 tied) | 1.271 GB | 635.7M | 13.6% | 쓴다 |
| **VISION** (`model.visual.*`) | **0.667 GB** | 333.5M | 7.2% | **한 번도 안 쓴다** |
| **MTP** (`mtp.*`) | **0.241 GB** | 120.6M | 2.6% | **한 번도 안 쓴다** |
| 합계 | 9.320 GB | 4659.9M | | |

- **VISION** — `config.json` 에 `vision_config`(ViT 24층, hidden 1024)가 있다.
  우리는 이미지를 보내지 않는다.
- **MTP** — multi-token prediction 헤드. 투기적 디코딩용이고 `generate()` 가
  호출하지 않는다.

계획서 §2.4 가 `Qwen-SEA-LION-v4-4B-VL` 후보를 두고 **"비전 타워가 VRAM 을
점유하는데 우리는 이미지를 보내지 않는다 — 순수 낭비"** 라며 2차로 미뤘는데,
같은 문제가 **확정한 모델 자체에 이미 있었다.** VL 이라는 이름이 붙지 않아
못 보고 지나간 것이다. 커밋 `a17bddc`(`vocab_size` 가 `config.text_config` 아래로
옮겨갔다)가 그 신호였는데, 그때는 "신형 config 스키마 전반"으로만 읽었다.

### 1.2 ★ 정정 — 런타임 메모리는 이미 절약되고 있었다

착수 시점에 "비전 타워를 떼면 런타임 메모리가 준다"고 적었는데 **틀렸다.**
실측으로 확인했다:

```
AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.5-4B")
  → Qwen3_5ForCausalLM · 4841.5M params · visual=False mtp=False · 427 keys
AutoModelForCausalLM.from_pretrained("models/qwen35-4b-textonly")
  → Qwen3_5ForCausalLM · 4841.5M params · visual=False mtp=False · 427 keys
```

**완전히 같다.** `MODEL_FOR_CAUSAL_LM_MAPPING_NAMES['qwen3_5']` 가
`Qwen3_5ForCausalLM` 이라, `local_client.py:281` 은 처음부터 텍스트 스택만
올리고 있었다 — 비전·MTP 가중치는 파일에 있을 뿐 메모리에 오르지 않았다.

따라서 **①의 이득은 런타임 메모리가 아니다.** 남는 것은:

| 얻는 것 | 값 |
|---|---|
| 디스크 | 9.32 → 8.43 GB (−0.89 GB) |
| 콜드스타트 읽기량 | 같은 만큼 감소 |
| AWS 전송량 | 같은 만큼 감소 |
| 런타임 메모리 | **0 (이미 절약되고 있었다)** |

메모리를 줄이는 것은 ②(양자화)뿐이다. ①은 그 전제를 만드는 무손실 정리 작업이고,
"산출물이 무엇인지에 대해 파일이 거짓말하지 않게" 만드는 값이 따로 있다.

### 1.3 노리는 것

1. **메모리** — ②에서만 나온다. 계획서 §3 의 표에서 M3 Pro 18GB 에 대해
   Qwen3.5-4B 는 "빠듯하나 가능"이다.
2. **비용** — `bench_local_speed.py` 의 논거가 이 작업의 가장 직접적인 동기다:
   > "$100 예산에서 GPU 상시 배포는 불가능하다(24/7 이면 월 ~$720). 그런데
   > **'로컬 생성'과 'GPU'는 같은 말이 아니다** — 외부 반출 0 의 조건은 로컬
   > 생성이지 GPU 가 아니다. CPU 로도 쓸 만한 지연이 나오면 데이터 주권 주장을
   > 유지한 채 월 ~$30 에 배포할 수 있다."
3. **로드 시간** — 커밋 `64dc422` 이 346초로 기록했다. 다만 그 값은 최초
   다운로드가 섞인 수치이고, 페이지 캐시가 더워진 뒤 재측정하면 한 자릿수 초다
   (§4.2). 콜드스타트에서 의미가 있는 것은 **읽어야 할 바이트 수**다.

---

## 2. 사전 검증 — 착수 전에 확인한 4건

**이 절이 이 문서의 핵심이다.** 나중에 산출물을 의심하게 될 때, 다시 소스를
뒤지지 않고 여기로 돌아올 수 있어야 한다.

### 2.1 텍스트 전용 클래스가 존재한다

```
transformers.models.qwen3_5 → Qwen3_5ForCausalLM  ✅
MODEL_FOR_CAUSAL_LM_MAPPING_NAMES['qwen3_5_text'] → Qwen3_5ForCausalLM
CONFIG_MAPPING_NAMES['qwen3_5_text']              → Qwen3_5TextConfig
```

→ `model_type: "qwen3_5_text"` 로 저장하면 `AutoModelForCausalLM.from_pretrained`
가 그대로 연다. **`local_client.py:281` 의 로드 경로를 고칠 필요가 없다.**

### 2.2 가중치 이름이 정확히 대응한다

`model.language_model.X` → `model.X` 단순 치환으로 **426개가 전부** 대응하고,
빠지는 것은 `lm_head.weight` 하나뿐이다(`tie_word_embeddings: true` 라 정상).
버려지는 것은 vision 297개 + mtp 15개. 분류되지 않는 키는 0개다.

### 2.3 ★ mrope 가 텍스트 전용에서 동일하게 동작한다 — 가장 위험했던 지점

`get_rope_index` · `compute_3d_position_ids` 는 **멀티모달 래퍼(`Qwen3_5Model`)에만**
있고 `Qwen3_5TextModel` 에는 없다. 텍스트 스택만 떼면 위치 인코딩이 달라질 수
있었다. 소스를 확인했다:

```python
# Qwen3_5TextRotaryEmbedding.forward
if position_ids.ndim == 2:
    position_ids = position_ids[None, ...].expand(3, position_ids.shape[0], -1)
```

텍스트 모델이 **2D position_ids 를 3개 mrope 격자로 동일 확장한다.**
`mrope_section: [11, 11, 10]` 의 t·h·w 세 축에 같은 텍스트 위치가 들어가므로,
이미지가 없는 입력에서는 멀티모달 경로의 `get_rope_index` 산출물과 같은 값이다.

**단, 이것은 코드를 읽은 결론이지 측정이 아니다.** 이 파이프라인에서 위치
인코딩이 어긋나면 예외가 아니라 조용한 품질 저하로 나타나므로(ADR-004 ① 및
`resolve_vocab_size` 주석과 같은 형태), `scripts/check_equivalence.py` 로 실제로
잰다 — §4.1.

### 2.4 torchao int8 이 MPS 에서 돈다 (⚠ 느린 것은 뒤에 드러났다 — §4.2)

```
quantize_(m, Int8WeightOnlyConfig())  →  Int8Tensor
m.to('mps') 후 forward                →  OK
```

`torchao 0.18.0` 이 venv 에 이미 있고(requirements.txt 미선언, torch 가 끌고 옴),
`transformers.TorchAoConfig` 가 있으며 `TorchAoHfQuantizer.is_serializable` 이
`True` 다 → `save_pretrained` 가 된다.

**새 의존성이 0 이다.** requirements.txt 가 이미 pip 충돌 경고 2건을 감수 중이라
(chromadb/tokenizers, xgrammar/transformers) 세 번째를 늘리지 않는 것이 값이 있다.
특히 xgrammar 쪽은 "문법 제약은 이 백엔드의 성패가 걸린 지점이라 더 조심해서
본다"고 적어 둔 자리다.

---

## 3. 방침

**두 단계를 거쳐 산출물 두 개를 만든다.** 런타임 코드는 손대지 않고
`LOCAL_MODEL` 경로만 바꾼다.

```
Qwen/Qwen3.5-4B  (9.32 GB, bf16)
      │
      │  ① 텍스트 전용 추출 — vision·MTP 를 버린다. 무손실.
      ▼
models/qwen35-4b-textonly       (8.43 GB, bf16)   ← 채택
      │
      │  ② torchao int8 weight-only — nn.Linear 만.
      ▼
models/qwen35-4b-textonly-int8  (4.87 GB)         ← AWS 로 이월 (§6.2)
```

> 결과: **①만 채택했다.** ②는 크기 목표를 달성했으나 MPS 에서 8.7배 느려
> 이 장치에서 판정할 수 없다 — §4.2 · §6.2.

### 3.1 왜 두 단계를 분리하는가

②가 품질 게이트를 못 넘겨도 ①은 남는다. 한 번에 하면 "품질이 떨어졌다"의
원인이 추출인지 양자화인지 가릴 수 없다.

### 3.2 왜 추출에서 샤드를 흘려 읽는가

전체 모델을 올린 뒤 텍스트부를 복사하면 9.32 + 8.41 = **17.7GB 가 동시에**
필요해 18GB 기계에서 죽는다. 계획서 §2 가 기록한 8B OOM(`.to()` 의 peak 2×)과
같은 형태다. 그래서 meta 장치로 골격만 만들고 `safetensors.safe_open` 으로
텐서 단위로 흘려 넣는다 — 비전·MTP 텐서는 메모리에 올라오지도 않는다.

### 3.3 왜 `lm_head` 는 양자화하지 않는가

`tie_word_embeddings: true` 라 임베딩(1.271GB)까지 함께 바뀐다. **로짓 차원을
건드리면 XGrammar 마스크가 에러 없이 어긋날 수 있는 자리다** —
`local_client.resolve_vocab_size` 의 주석이 경고하는 바로 그 사고 형태이고,
ADR-004 ① 의 `citations` 생략과 같은 종류의 조용한 실패다.

그래서 transformers 기본값(lm_head 제외)을 그대로 둔다. 대신 예상 크기가
4.4GB 가 아니라 **4.5~5.0GB** 다.

### 3.4 하이브리드 어텐션은 자동으로 보호된다

`Int8WeightOnlyConfig` 는 `nn.Linear` 만 바꾼다. `layer_types` 가
`linear_attention ×3 + full_attention` 을 8회 반복하는 구조인데, 선형 어텐션의
`A_log`·`dt_bias`·`conv1d`(nn.Conv1d)와 norm 은 Linear 가 아니라 제외된다 —
`mamba_ssm_dtype: float32` 전제가 지켜진다.

---

## 4. 측정

### 4.1 추출 동일성 (`check_equivalence.py`)

게이트: **그리디 토큰열 완전 일치.** 어긋나면 추출이 틀린 것이므로 int8 로
넘어가지 않는다.

| 프롬프트 | 토큰열 | 로짓 최대차 |
|---|---|---|
| ③ 정규화 | **일치** | **0.000e+00** |
| ⑦ 생성 | **일치** | **0.000e+00** |

**로짓 차이가 정확히 0 이다 — 비트 단위로 같다.** §2.3 의 mrope 우려는 근거가
없었음이 측정으로 확인됐다. 추출은 무손실이다.

> 다만 §1.2 가 밝힌 대로, `AutoModelForCausalLM` 이 원본에서도 텍스트 스택만
> 올리고 있었으므로 **두 모델은 애초에 같은 가중치였다.** 0 이 나온 것은 놀라운
> 결과가 아니라 당연한 결과다. 이 검사의 값은 "추출 스크립트가 가중치를
> 뒤섞거나 빠뜨리지 않았다"를 증명하는 데 있다.

측정 dtype 은 **bfloat16** 이다. fp32 로 재려 했으나 원본이 18.6GB 라 이 기계에서
스왑이 고갈됐다(실측: 15.4GB 중 528MB 만 남음) — 계획서 §2 의 8B SIGBUS 와 같은
조건이다. 두 모델을 같은 dtype 으로 재므로 남는 차이가 곧 추출의 차이이고,
bf16 은 실제 CUDA 배포 dtype 이라 오히려 현실적이다.

### 4.2 크기 · 속도 (`bench_local_speed.py --device mps`, M3 Pro 18GB)

| 산출물 | 디스크 | 로드 | ③ 정규화 | ⑦ 생성 | TTFT | 본문 |
|---|---:|---:|---:|---:|---:|---:|
| 원본 (fp16) | 9.32 GB | 28초 | 2.4초 | 194.8초 | 6.0초 | 151자 |
| **textonly** (fp16) | **8.43 GB** | **17초** | **2.4초** | **197.5초** | **6.0초** | **151자** |
| textonly-int8 | 4.87 GB | 15초 | **20.8초** | **측정 중단** | — | — |

**① textonly — 무손실이 실측으로도 확인됐다.** ③ 검색어가 글자 단위로 같고
(`'계좌 한도 해제 신청 시 필요한 서류 및 절차'`), ⑦ 답변도 같은 151자다.
⑦ 생성의 194.8 vs 197.5초 차이는 측정 오차다(워밍업은 193.7 vs 194.0). 로드가
28 → 17초로 준 것이 읽어야 할 바이트가 0.89GB 줄어든 효과다.

**② int8 — 크기는 목표대로, MPS 속도는 실패다.**

    크기      8.43 → 4.87 GB   (−42%, 예상 4.5~5.0GB 범위 안)
    ③ 정규화   2.4 → 20.8초     ★ 8.7배 느림
    ⑦ 생성    22분 넘게 미완 → 중단

`verify_local_model.py` 6개 검사는 전부 통과했다 — 로드(mps, `device_map` 없이),
XGrammar 컴파일 1.3초, 문법 제약 생성에서 `citations [1]` · `numbers_used
['100만원']` · `tier=A`. **배선과 품질이 아니라 속도만의 문제다.**

원인은 **MPS 에 int8 행렬곱 커널이 없다**는 것으로 보인다. torchao 가 매 행렬곱마다
가중치를 fp16 으로 역양자화하므로, 메모리 대역폭을 아낀 만큼을 연산으로 도로
낸다. CUDA 는 int8 텐서코어가 있어 그림이 뒤집힐 수 있다.

### 4.3 vi 41문항 스크리닝 — 돌리지 않는다

계획 단계에서는 textonly·int8 각각에 대해 계획서 §7.9 의 vi 41문항을 돌릴
예정이었다. **둘 다 돌리지 않기로 했고, 이유가 서로 다르다.**

**textonly — 돌릴 필요가 없다.** §4.1 에서 로짓이 비트 단위로 같음을 보였고
§4.2 에서 ③ 검색어와 ⑦ 답변이 글자 단위로 같음을 확인했다. 같은 입력에
같은 출력을 내는 모델을 41문항 돌리면 **exp_008 대조군을 그대로 재생산할 뿐**
2시간을 쓴다. 무손실 증명이 스크리닝을 대체한다.

**int8 — 이 장치에서는 돌릴 수 없다.** ③ 이 8.7배 느리므로 41문항이
**18시간 규모**다. 계획서 §7.9 가 잡은 "30분~1시간"과 자릿수가 다르다.

→ **int8 의 품질 판정은 AWS(CUDA)로 이월한다.** 계획서 §8 이 "판정은 CUDA bf16
리포트로만 한다"고 못박아 둔 것과 어긋나지 않는다 — MPS 수치는 원래 스크리닝
용도였고, 그 스크리닝이 이 변형에 대해서만 성립하지 않을 뿐이다.

AWS 에서 잴 때 볼 값(임계값 비의존이라 직접 비교 가능 — 계획서 §5):

| 지표 | 기준 |
|---|---|
| Recall@5 (vi) | 대조군 대비 하락 없을 것 |
| 근거 인용률 | 100% |
| 숫자 정확도 | ≥98% |
| 출력 언어 일치 | ≥95% |
| 금지표현 | 0건 |

특히 **`citations` 누락**을 본다. 지시 준수 저하는 이 파이프라인에서 조용한
실패로 나타난다 — 인용 0건 → 후처리 차단 → 정상 답변이 `no_citation` 폴백.

**리포트 명명** — 계획서 §7.10 의 `exp_NNN_{model}_{device}` 에서 `{model}` 자리에
빌드 변형명을 넣는다: `exp_009_qwen35-4b-textonly-int8_cuda.json`.

**비교 규칙** — 계획서 §6.1 은 fp16/bf16 차이만으로도 같은 표에 못 놓는다고
못박아 두었다. 양자화는 그보다 큰 수치 변화다. int8 리포트를 다른 후보와 같은
표에 섞지 않고 별도 행으로 둔다.

---

## 5. 실패할 수 있는 곳

착수 전에 꼽아 둔 다섯 가지 중 **하나가 실제로 터졌고, 그것이 결론을 바꿨다.**

| 지점 | 예상한 증상 | 실제 |
|---|---|---|
| torchao safetensors 직렬화 | `save_pretrained` 예외 | 문제없음. 폴백 불필요 |
| MPS 양자화 로드가 `device_map` 요구 | SIGSEGV(139) | 문제없음. `.to("mps")` 로 15초에 로드 |
| XGrammar `TokenizerInfo` | 문법이 조용히 꺼짐 → `citations` 소실 | 문제없음. 6개 검사 통과, `citations [1]` |
| **int8 이 fp16 보다 느림** | TTFT 증가 | **터졌다. ③ 8.7배, ⑦ 측정 불가** |
| int8 품질 저하 | vi Recall·인용률 하락 | **판정 못 함** — 속도 때문에 재지 못했다 |

예상하지 못한 것이 하나 더 있었다: **런타임 메모리 이득이 애초에 없었다**(§1.2).

`textonly` 와 `int8` 을 분리된 산출물로 둔 판단이 여기서 값을 했다. ②가 막혔지만
①은 그대로 채택할 수 있었고, "무엇이 원인인가"를 가릴 필요도 없었다.

### 5.1 다음에 시도할 것 (AWS 에서)

- **CUDA int8** — 텐서코어가 있으므로 MPS 결과가 뒤집힐 수 있다. 이것이 본선이다.
- MPS 를 계속 쓰고 싶다면 `Int8DynamicActivationIntxWeightConfig` ·
  `IntxWeightOnlyConfig` 등 torchao 의 저비트 경로가 Metal 커널을 타는지 확인한다.
  다만 **이 프로젝트에서 MPS 는 스크리닝용**이므로(계획서 §6) 우선순위는 낮다.

---

## 6. 결정 (2026-08-18)

### 6.1 textonly 를 채택한다

무손실이 두 겹으로 증명됐고(로짓 0 차이 · 출력 문자열 동일), 디스크 −0.89GB 와
로드 −11초를 공짜로 얻는다. 잃는 것이 없다.

### 6.2 int8 은 AWS 로 이월한다 — 탈락이 아니다

크기 목표(8.43 → 4.87GB, −42%)는 달성했고 배선·문법·필드 채움도 전부 통과했다.
**MPS 에 int8 커널이 없다는 장치 문제**로 이 기계에서 판정할 수 없을 뿐이다.
계획서 §2 가 8B 두 후보를 두고 "후보에서 탈락한 것이 아니다. 첫 검증을 AWS 에서
한다"고 적은 것과 **같은 형태의 이월**이다.

산출물 `models/qwen35-4b-textonly-int8` 은 지운다고 얻는 것이 없으므로 남겨 두고,
`export_local_model.py --stage int8` 로 AWS 에서 언제든 다시 만든다.

### 6.3 `config.py` 기본값은 바꾸지 않는다

`local_model` 은 `Qwen/Qwen3.5-4B` 로 둔다. textonly 가 무손실이라 **품질을
근거로는 바꿔도 되지만**, 다른 이유로 바꾸지 않는다:

- `models/…` 는 **새로 받은 체크아웃에 존재하지 않는다.** 기본값으로 두면
  `export_local_model.py` 를 먼저 돌리지 않은 사람에게 기동 실패가 난다 —
  `local_model` 기본값이 gated repo 라 `LLM_BACKEND=local` 이 죽어 있던
  커밋 `64dc422` 의 사고와 **같은 형태**다.
- `Dockerfile` 은 현재 임베딩 모델만 이미지에 굽는다. 생성 모델의 배포 경로가
  정해지기 전에 기본값만 로컬 경로로 옮기면 그 불일치가 숨는다.

→ **옵트인으로 쓴다.** `config.py:100` 주석에 만드는 법을 적어 두었다:

```bash
python apps/api/scripts/export_local_model.py --stage textonly
LOCAL_MODEL=models/qwen35-4b-textonly
```

배포 경로(Docker 에 굽기 vs 볼륨 마운트)가 정해질 때 기본값을 함께 다룬다.

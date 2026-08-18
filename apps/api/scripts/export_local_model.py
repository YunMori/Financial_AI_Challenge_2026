"""로컬 생성 모델 슬림화 — 안 쓰는 부분을 버리고 양자화한다.

    python apps/api/scripts/export_local_model.py --stage textonly
    python apps/api/scripts/export_local_model.py --stage int8

왜 줄이는가
-----------
`Qwen/Qwen3.5-4B` 는 `Qwen3_5ForConditionalGeneration` 이다 — **비전 타워(ViT
24층)와 투기적 디코딩 헤드(MTP)를 달고 다닌다.** 우리는 이미지를 보내지 않고
`generate()` 는 MTP 를 호출하지 않는다. safetensors 헤더로 잰 실측:

    TEXT_LM   7.140 GB  3570.1M  76.6%   쓴다
    EMBED     1.271 GB   635.7M  13.6%   쓴다 (lm_head 와 tied)
    VISION    0.667 GB   333.5M   7.2%   한 번도 안 쓴다
    MTP       0.241 GB   120.6M   2.6%   한 번도 안 쓴다
    ────────────────────────────────────
              9.320 GB  4659.9M

계획서 §2.4 가 SEA-LION VL 후보를 두고 "비전 타워가 VRAM 을 점유하는데 우리는
이미지를 보내지 않는다 — 순수 낭비"라고 적어 둔 문제가 **확정한 모델 자체에
이미 있었다.**

두 단계를 분리해 둔 이유
------------------------
    원본 ──① textonly──▶ 무손실 (-0.89GB)  ──② int8──▶ 손실 있음 (-3.56GB 더)

②가 품질 게이트를 못 넘겨도 ①은 남는다. 한 번에 하면 "품질이 떨어졌다"의 원인이
추출인지 양자화인지 가릴 수 없다 — `scripts/check_equivalence.py` 가 ①을 따로
증명하는 것도 같은 이유다. **이 분리가 실제로 값을 했다** — 아래 실측 참조.

실측 결과 (2026-08-18 · MPS · M3 Pro 18GB)
-------------------------------------------
    산출물          디스크    로드    ③ 정규화   결정
    원본            9.32 GB   28초    2.4초     기준선
    textonly        8.43 GB   17초    2.4초     ★ 채택 — 로짓 최대차 0, 출력 동일
    textonly-int8   4.87 GB   15초   20.8초     AWS 로 이월

★ **①의 이득은 런타임 메모리가 아니다.** `AutoModelForCausalLM` 은 원본에서도
  텍스트 스택만 올린다(양쪽 다 4841.5M params · visual=False). 남는 것은
  디스크·콜드스타트 읽기량·AWS 전송량이다.

⚠ **②는 MPS 에서 8.7배 느리다.** 배선·문법·필드 채움은 전부 통과했으므로 품질이
  아니라 장치 문제다 — MPS 에 int8 행렬곱 커널이 없어 매 연산마다 fp16 으로
  역양자화한다. CUDA 는 텐서코어가 있어 뒤집힐 수 있으므로 **탈락이 아니라 이월**이다.

상세: `docs/2026-08-18-model-slimming.md`
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SRC = "Qwen/Qwen3.5-4B"
TEXTONLY_DIR = REPO_ROOT / "models" / "qwen35-4b-textonly"
INT8_DIR = REPO_ROOT / "models" / "qwen35-4b-textonly-int8"

# 텍스트 스택은 체크포인트에서 이 접두사 아래 있다. 벗기면 Qwen3_5ForCausalLM 의
# 이름과 정확히 일치한다 — 426개 전부, 남는 것은 tied 라 저장되지 않는 lm_head 뿐.
TEXT_PREFIX = "model.language_model."

# 토크나이저는 가중치와 함께 가야 한다. save_pretrained 가 모델 것만 쓰므로
# 나머지는 직접 복사한다. chat_template.jinja 를 빠뜨리면 프롬프트 조립이
# 통째로 실패한다.
#
# ★ `preprocessor_config.json` · `video_preprocessor_config.json` 은 **일부러 뺀다.**
#   이미지·비디오 전처리 설정인데 비전 타워를 버린 모델에 남겨 두면, 산출물이
#   무엇인지에 대해 파일이 거짓말을 한다. generation_config.json 은 save_pretrained
#   가 직접 쓴다.
SIDECAR_FILES = (
    "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
    "special_tokens_map.json", "chat_template.jinja",
)


def _human(nbytes: int) -> str:
    return f"{nbytes / 1e9:.2f} GB"


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _resolve_source(src: str) -> Path:
    """HF 리포 ID 든 로컬 경로든 **스냅샷 디렉터리**로 바꾼다.

    샤드를 텐서 단위로 열어야 해서(아래 이유) 실제 파일 경로가 필요하다.
    이미 캐시에 있으면 네트워크를 타지 않는다.
    """
    p = Path(src)
    if p.is_dir():
        return p
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(src))


# ── ① 텍스트 전용 추출 ───────────────────────────────────────────────

def export_textonly(src: str, dst: Path) -> None:
    """비전·MTP 를 버리고 `Qwen3_5ForCausalLM` 체크포인트로 다시 쓴다.

    ★ **전체 모델을 올린 뒤 텍스트부를 복사하면 안 된다.** 9.32 + 8.41 =
      17.7GB 가 동시에 필요해 18GB 기계에서 죽는다 — 계획서 §2 가 기록한 8B
      OOM 과 같은 형태다(`.to(device)` 의 peak 2× 사고도 같은 뿌리).

    그래서 **meta 장치로 골격만 만들고 샤드를 텐서 단위로 흘려 넣는다.**
    비전·MTP 텐서는 메모리에 올라오지도 않으므로 최대 사용량이 산출물 크기와
    같아진다(≈8.4GB).
    """
    import torch
    from safetensors import safe_open
    from transformers import AutoConfig
    from transformers.models.qwen3_5 import Qwen3_5ForCausalLM

    snapshot = _resolve_source(src)
    print(f"  원본: {snapshot}")

    # `text_config` 는 Qwen3_5TextConfig 이고 model_type 이 'qwen3_5_text' 다.
    # 그 값이 AutoModelForCausalLM → Qwen3_5ForCausalLM 로 매핑되므로,
    # 저장 후 local_client.py 의 로드 경로를 고칠 필요가 없다.
    text_config = AutoConfig.from_pretrained(snapshot).text_config
    text_config.architectures = ["Qwen3_5ForCausalLM"]

    started = time.perf_counter()
    with torch.device("meta"):
        model = Qwen3_5ForCausalLM(text_config)
    want = set(model.state_dict().keys())

    shards = sorted(snapshot.glob("model*.safetensors"))
    if not shards:
        raise SystemExit(f"safetensors 샤드를 찾지 못했습니다: {snapshot}")

    loaded: dict[str, torch.Tensor] = {}
    dropped = {"vision": 0, "mtp": 0, "other": 0}
    for shard in shards:
        with safe_open(shard, framework="pt") as f:
            for key in f.keys():  # noqa: SIM118 - safe_open 은 keys() 만 노출한다
                if key.startswith(TEXT_PREFIX):
                    loaded["model." + key[len(TEXT_PREFIX):]] = f.get_tensor(key)
                elif "visual" in key:
                    dropped["vision"] += 1
                elif key.startswith("mtp"):
                    dropped["mtp"] += 1
                else:
                    dropped["other"] += 1
        print(f"    {shard.name} — 누적 {len(loaded)}개")

    if dropped["other"]:
        raise SystemExit(
            f"분류하지 못한 가중치 {dropped['other']}개가 있습니다. "
            "모델 구조가 바뀌었을 수 있으니 확인 없이 진행하지 않습니다."
        )

    # lm_head 는 tie_word_embeddings=True 라 체크포인트에 없다. 그것 **하나만**
    # 빠져야 정상이다 — 다른 것이 빠지면 접두사 규칙이 틀린 것이다.
    missing = want - set(loaded)
    if missing != {"lm_head.weight"}:
        raise SystemExit(f"예상과 다른 누락: {sorted(missing)[:8]}")
    extra = set(loaded) - want
    if extra:
        raise SystemExit(f"예상과 다른 잉여: {sorted(extra)[:8]}")

    # assign=True — meta 텐서를 실제 텐서로 **교체**한다. 복사가 아니라서
    # 최대 메모리가 두 배가 되지 않는다.
    model.load_state_dict(loaded, strict=False, assign=True)
    model.tie_weights()
    model.eval()

    print(f"  텍스트 {len(loaded)}개 로드 · 버림 vision {dropped['vision']} / "
          f"mtp {dropped['mtp']} — {time.perf_counter() - started:.0f}초")

    dst.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(dst)
    _copy_sidecars(snapshot, dst)


def _copy_sidecars(snapshot: Path, dst: Path) -> None:
    copied = []
    for name in SIDECAR_FILES:
        srcf = snapshot / name
        if srcf.exists():
            shutil.copy2(srcf, dst / name)
            copied.append(name)
    print(f"  부속 파일 {len(copied)}개 복사: {', '.join(copied)}")


# ── ② int8 양자화 ────────────────────────────────────────────────────

def export_int8(src: Path, dst: Path) -> None:
    """torchao int8 weight-only. `nn.Linear` 만 바뀐다.

    **`quantize_()` 를 직접 부르지 않고 `quantization_config` 로 로드한다.**
    그래야 transformers 가 `config.json` 에 양자화 설정을 기록하고, 재로드가
    자동으로 복원된다 — `local_client._load()` 가 아무것도 몰라도 된다.

    ★ `lm_head` 는 양자화하지 않는다(transformers 기본값). tied 라 임베딩까지
      함께 바뀌는데, 로짓 차원을 건드리면 XGrammar 마스크가 **에러 없이** 어긋날
      수 있는 자리다(`local_client.resolve_vocab_size` 주석 참조). 그래서 예상
      크기는 4.4GB 가 아니라 4.5~5.0GB 다.

    하이브리드 어텐션의 `A_log`·`dt_bias`·`conv1d`(nn.Conv1d)와 norm 은
    Linear 가 아니라 자동으로 제외된다 — `mamba_ssm_dtype: float32` 전제가 지켜진다.
    """
    import torch
    from torchao.quantization import Int8WeightOnlyConfig
    from transformers import AutoModelForCausalLM, TorchAoConfig

    if not src.exists():
        raise SystemExit(f"먼저 --stage textonly 를 돌리세요: {src} 없음")

    started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        src, dtype=torch.bfloat16,
        # transformers 5 는 문자열 지정("int8_weight_only")을 더는 받지 않는다.
        # torchao 의 Config 객체를 직접 넘겨야 한다.
        quantization_config=TorchAoConfig(Int8WeightOnlyConfig()),
    )
    print(f"  양자화 로드 — {time.perf_counter() - started:.0f}초")

    dst.mkdir(parents=True, exist_ok=True)
    try:
        model.save_pretrained(dst)
    except Exception as e:  # noqa: BLE001
        # torchao 텐서 서브클래스는 safetensors 직렬화가 버전마다 갈린다.
        # 여기서 막히면 pickle 로 물러난다 — 산출물은 우리가 만든 것이라
        # 신뢰 문제가 없다.
        print(f"  ⚠️  safetensors 실패({type(e).__name__}) → safe_serialization=False")
        model.save_pretrained(dst, safe_serialization=False)
    _copy_sidecars(src, dst)


# ── 진입점 ───────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", required=True, choices=["textonly", "int8"])
    ap.add_argument("--src", default=DEFAULT_SRC, help="원본 리포 ID 또는 경로")
    ap.add_argument("--out", default=None, help="산출물 경로 (기본: models/…)")
    args = ap.parse_args()

    if args.stage == "textonly":
        dst = Path(args.out) if args.out else TEXTONLY_DIR
        print(f"\n① 텍스트 전용 추출 → {dst}")
        print("─" * 62)
        export_textonly(args.src, dst)
    else:
        src = Path(args.src) if args.src != DEFAULT_SRC else TEXTONLY_DIR
        dst = Path(args.out) if args.out else INT8_DIR
        print(f"\n② int8 양자화 {src} → {dst}")
        print("─" * 62)
        export_int8(src, dst)

    size = _dir_size(dst)
    print(f"\n  산출물 {_human(size)} — {dst}")
    print(f"  사용: LOCAL_MODEL={dst.relative_to(REPO_ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

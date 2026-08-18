"""텍스트 전용 추출이 출력을 바꾸지 않았음을 증명한다.

    python apps/api/scripts/check_equivalence.py --capture original
    python apps/api/scripts/check_equivalence.py --capture textonly
    python apps/api/scripts/check_equivalence.py --compare

왜 필요한가
-----------
`export_local_model.py --stage textonly` 는 `Qwen3_5ForConditionalGeneration` 에서
텍스트 스택만 떼어 `Qwen3_5ForCausalLM` 로 다시 쓴다. 무손실이라고 **주장**하지만,
위치 인코딩이 한 군데 갈릴 수 있었다:

    Qwen3_5Model.get_rope_index / compute_3d_position_ids   ← 멀티모달 래퍼에만 있다
    Qwen3_5TextModel                                        ← 없다

소스를 보면 텍스트 모델이 스스로 처리한다:

    # Qwen3_5TextRotaryEmbedding.forward
    if position_ids.ndim == 2:
        position_ids = position_ids[None, ...].expand(3, position_ids.shape[0], -1)

2D 위치를 3개 mrope 격자로 **동일 확장**하므로, 이미지가 없는 입력에서는 멀티모달
경로의 산출물과 같은 값이다. 하지만 그건 코드를 읽은 결론이지 측정이 아니다.
이 파이프라인에서 위치 인코딩이 어긋나면 예외가 아니라 **조용한 품질 저하**로
나타나므로(ADR-004 ①·`resolve_vocab_size` 주석과 같은 형태), 실제로 잰다.

추출과 양자화를 한 번에 재면 "품질이 떨어졌다"의 원인을 가릴 수 없다.
이 검사는 **추출만** 본다.

왜 두 번 나눠 실행하는가 · 왜 bf16 인가
--------------------------------------
두 모델을 동시에 올릴 수 없어 순차로 올리고 로짓을 파일에 남긴 뒤 비교한다.

**dtype 은 bfloat16 이다 — float32 로 재려다 실패했다.** 원본을 fp32 로 올리면
18.6GB 라 M3 Pro 18GB 에서 스왑이 고갈된다(실측: 15.4GB 중 528MB 만 남음).
계획서 §2 가 8B `device_map` 시도에서 기록한 SIGBUS 와 같은 조건이다.

fp32 를 포기해도 이 검사는 성립한다. **두 모델을 같은 dtype 으로 재기 때문**이다 —
계획서 §6.1 이 경고하는 것은 서로 다른 dtype 의 수치를 같은 표에 놓는 것이고,
여기서는 dtype 이 공통이라 남는 차이가 곧 추출의 차이다. 오히려 bf16 은 실제
CUDA 배포 dtype 이라 더 현실적이다.

장치는 cpu 가 기본이다. MPS 커널 차이를 변수에서 뺀다.

게이트
------
    그리디 토큰열 완전 일치     ← 어긋나면 추출이 틀린 것이다. int8 로 넘어가지 않는다
    로짓 최대 절대차            ← 참고값. 같은 가중치·같은 dtype 이므로 0 이어야 한다
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[3]
ORIGINAL = "Qwen/Qwen3.5-4B"
TEXTONLY = REPO_ROOT / "models" / "qwen35-4b-textonly"
CACHE = Path(__file__).resolve().parents[1] / ".cache_equivalence"

# 새로 뽑는 토큰 수. 발산은 초기에 드러나므로 길게 갈 필요가 없다 —
# 한 토큰이 갈리면 이후 생성 전체가 갈라진다(계획서 §6.1).
N_NEW = 24


def _prompts() -> list[tuple[str, list[dict]]]:
    """파이프라인이 실제로 보내는 두 프롬프트. 합성 입력이 아니다."""
    from app.llm.prompts import normalize_prompt, system_prompt, user_message
    from app.schemas.common import Lang

    q = "Tôi cần giấy tờ gì để gỡ bỏ hạn mức tài khoản?"
    evidence = "\n\n".join(
        f"[근거 {i}] 금융위원회 「한도제한계좌 개선방안」 (발행 2024-05-02 / 확인 2026-08-12)\n"
        f"한도제한계좌를 보유한 고객은 하루에 인터넷뱅킹 100만원, ATM 100만원, "
        f"창구거래 300만원까지 거래할 수 있다. 한도를 해제하려면 급여이체 확인서, "
        f"재직증명서, 사업자등록증 등 거래 목적을 증명하는 서류가 필요하다."
        for i in range(1, 6)
    )
    return [
        ("③ 정규화", [{"role": "user", "content": normalize_prompt(q, "vi")}]),
        ("⑦ 생성", [
            {"role": "system", "content": system_prompt(Lang.VI)},
            {"role": "user", "content": user_message(
                question=q, context_block=evidence, visa="E-9", stay=None, purposes=[])},
        ]),
    ]


def capture(which: str, device: str) -> None:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    src = ORIGINAL if which == "original" else str(TEXTONLY)
    print(f"\n  {which} 로드: {src}")
    started = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(src)
    # bf16 이다 — fp32 는 원본이 18.6GB 라 이 기계에서 스왑이 고갈된다(docstring 참조).
    # 두 모델을 같은 dtype 으로 재므로 남는 차이가 곧 추출의 차이다.
    model = AutoModelForCausalLM.from_pretrained(src, dtype=torch.bfloat16).to(device)
    model.eval()
    print(f"  로드 완료 — {time.perf_counter() - started:.0f}초 "
          f"({type(model).__name__})")

    CACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    for label, messages in _prompts():
        enc = tok.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt",
            return_dict=True, enable_thinking=False,
        ).to(device)
        with torch.no_grad():
            # 비교는 fp32 로 올려서 한다 — 저장 dtype 때문에 차이가 반올림되어
            # 숨는 것을 막는다.
            logits = model(**enc).logits[0, -1].float().cpu()
            gen = model.generate(**enc, max_new_tokens=N_NEW, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        new_tokens = gen[0][enc["input_ids"].shape[-1]:].cpu()
        out[label] = {"logits": logits, "tokens": new_tokens}
        print(f"    {label} — {tok.decode(new_tokens, skip_special_tokens=True)[:52]!r}")

    torch.save(out, CACHE / f"{which}.pt")
    print(f"  저장: {CACHE / f'{which}.pt'}")


def compare() -> int:
    import torch

    paths = {w: CACHE / f"{w}.pt" for w in ("original", "textonly")}
    for w, p in paths.items():
        if not p.exists():
            raise SystemExit(f"먼저 --capture {w} 를 돌리세요 ({p} 없음)")
    a = torch.load(paths["original"], weights_only=False)
    b = torch.load(paths["textonly"], weights_only=False)

    print(f"\n  {'프롬프트':<12} {'토큰열':<10} {'로짓 최대차':>12}")
    print("  " + "─" * 40)
    ok = True
    for label in a:
        same = torch.equal(a[label]["tokens"], b[label]["tokens"])
        delta = (a[label]["logits"] - b[label]["logits"]).abs().max().item()
        ok &= same
        print(f"  {label:<12} {'일치' if same else '불일치':<10} {delta:>12.3e}")
        if not same:
            print(f"      원본     {a[label]['tokens'].tolist()}")
            print(f"      textonly {b[label]['tokens'].tolist()}")

    print()
    if ok:
        print("  ✅ 그리디 토큰열이 전부 일치한다 — 추출은 무손실이다.")
        print("     → int8 단계로 진행해도 된다.\n")
        return 0
    print("  ❌ 토큰열이 갈렸다 — 추출이 틀렸다. int8 로 넘어가지 않는다.")
    print("     위치 인코딩(mrope) 또는 가중치 대응을 먼저 의심한다.\n")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", choices=["original", "textonly"])
    ap.add_argument("--compare", action="store_true")
    # cpu 가 기본이다 — MPS 커널 차이를 변수에서 뺀다.
    ap.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    args = ap.parse_args()

    if args.capture:
        capture(args.capture, args.device)
        return 0
    if args.compare:
        return compare()
    ap.error("--capture 또는 --compare 중 하나가 필요합니다")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""로컬 생성 속도 측정 — 배포 장치 선택의 입력 (ADR-004).

    python apps/api/scripts/bench_local_speed.py --device cpu --dtype bfloat16
    python apps/api/scripts/bench_local_speed.py --device cuda

왜 재는가
---------
$100 예산에서 GPU 상시 배포는 불가능하다(24/7 이면 월 ~$720). 그런데
**"로컬 생성"과 "GPU"는 같은 말이 아니다** — 외부 반출 0 의 조건은 로컬 생성이지
GPU 가 아니다. CPU 로도 쓸 만한 지연이 나오면 데이터 주권 주장을 유지한 채
월 ~$30 에 배포할 수 있다.

그래서 재는 것은 하나다: **이용자가 기다릴 수 있는 시간 안에 답이 나오는가.**

무엇을 재는가
-------------
파이프라인이 실제로 하는 두 호출을 그대로 잰다. 합성 벤치마크가 아니다.

    ③ 정규화   짧은 프롬프트 → 검색어 (128 토큰 상한)
    ⑦ 생성     시스템 + 근거 5건 → 구조화 답변 (문법 제약)

⚠ 이 기계의 수치는 AWS 인스턴스와 다르다. 아키텍처도 메모리 대역폭도 다르다.
  **자릿수를 잡는 용도**이지 배포 사양을 확정하는 값이 아니다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 근거 5건짜리 현실적인 컨텍스트. 실제 파이프라인의 ⑥ 조립 결과와 같은 모양이다.
EVIDENCE = "\n\n".join(
    f"[근거 {i}] 금융위원회 「한도제한계좌 개선방안」 (발행 2024-05-02 / 확인 2026-08-12)\n"
    f"한도제한계좌를 보유한 고객은 하루에 인터넷뱅킹 100만원, ATM 100만원, "
    f"창구거래 300만원까지 거래할 수 있다. 한도를 해제하려면 급여이체 확인서, "
    f"재직증명서, 사업자등록증 등 거래 목적을 증명하는 서류가 필요하다. "
    f"제출 서류는 거래 목적과 체류자격에 따라 달라진다."
    for i in range(1, 6)
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--dtype", default="auto",
                    choices=["auto", "float16", "bfloat16", "float32"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--runs", type=int, default=2, help="생성 반복 (첫 회는 워밍업)")
    args = ap.parse_args()

    from app.config import get_settings
    from app.llm.local_client import LocalLLMClient, resolve_dtype
    from app.llm.prompts import normalize_prompt, system_prompt, user_message
    from app.schemas.common import Lang

    s = get_settings()
    c = LocalLLMClient(model_name=args.model, device=args.device, dtype=args.dtype)
    dt = str(resolve_dtype(c.device, args.dtype)).replace("torch.", "")
    print(f"\n{c.model_name} · device={c.device} · dtype={dt}")
    print(f"max_new_tokens={s.local_max_new_tokens}\n{'─' * 62}")

    t0 = time.perf_counter()
    c._load()  # noqa: SLF001
    print(f"  로드                 {time.perf_counter() - t0:6.0f}초")

    t0 = time.perf_counter()
    c.answer_grammar  # noqa: B018
    print(f"  문법 컴파일           {time.perf_counter() - t0:6.1f}초\n")

    # ── ③ 정규화 ────────────────────────────────────────────────────
    q = "Tôi cần giấy tờ gì để gỡ bỏ hạn mức tài khoản?"
    t0 = time.perf_counter()
    terms = asyncio.run(c.translate_to_search_terms(q, "vi"))
    norm_s = time.perf_counter() - t0
    print(f"  ③ 정규화             {norm_s:6.1f}초  → {terms[:44]!r}")

    # ── ⑦ 생성 ──────────────────────────────────────────────────────
    system = system_prompt(Lang.VI)
    user = user_message(question=q, context_block=EVIDENCE, visa="E-9",
                        stay=None, purposes=[])

    async def once():
        chars = 0
        started = time.perf_counter()
        first = None
        answer = ""
        async for ev in c.stream(system=system, user=user, lang=Lang.VI):
            if ev.kind == "token":
                if first is None:
                    first = time.perf_counter() - started
                chars += len(ev.text)
            elif ev.kind == "final":
                answer = ev.result.answer.answer
        return time.perf_counter() - started, first, chars, answer

    best = None
    for i in range(args.runs):
        total, ttft, chars, answer = asyncio.run(once())
        tag = "워밍업" if i == 0 and args.runs > 1 else f"{i + 1}회차"
        print(f"  ⑦ 생성 ({tag})      {total:6.1f}초  TTFT {ttft or 0:5.1f}초  "
              f"본문 {len(answer):4}자")
        if i > 0 or args.runs == 1:
            best = (total, ttft, answer)

    total, ttft, answer = best
    print(f"\n  답변: {answer[:76]}{'…' if len(answer) > 76 else ''}")

    # ── 판정 ────────────────────────────────────────────────────────
    e2e = norm_s + total
    print(f"\n{'─' * 62}")
    print(f"  이용자 체감 (③+⑦)    {e2e:6.1f}초   목표 P95 6초")
    print(f"  TTFT                 {ttft or 0:6.1f}초   planner §14.2 가 관리하라는 값")
    print()
    if e2e <= 6:
        print("  ✅ 지연 예산 안. 이 장치로 배포 가능하다.")
    elif e2e <= 20:
        print("  ⚠️  예산 초과지만 데모로는 쓸 만하다. 진행 표시가 필수다 —")
        print("     §15.3 이 지적한 '심사위원이 처음 접속했을 때' 문제와 같은 구간.")
    else:
        print("  ❌ 데모로 쓰기 어렵다. 이 장치로는 배포하지 않는다.")
    print(f"\n  ⚠ 이 기계의 수치다. AWS 인스턴스와 다르다 — 자릿수만 본다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""생성 경로 검증 — 실제 Anthropic 호출로 7가지를 잰다.

M1 은 `NullLLMClient` 로 "파이프라인이 죽지 않는다"만 확인했다. 이 스크립트는
**실제로 한 번 돌려서** 설계 가정이 맞는지 본다. 여기서 나오는 결과가 프롬프트·
effort·모델 선택을 바꿀 수 있다.

모델이나 프롬프트를 바꿀 때마다 다시 돌린다.

    python apps/api/scripts/verify_generation.py            # 전체
    python apps/api/scripts/verify_generation.py --only cache
    python apps/api/scripts/verify_generation.py --effort low

검증 항목
---------
1. 시스템 프롬프트 토큰 수  — sonnet-5 캐시 하한 1,024 를 넘는가
2. 프롬프트 캐시 적중       — 2회차에 cache_read_input_tokens > 0 인가
3. 구조화 출력 파싱         — 10회 모두 LLMAnswer 로 검증되는가
4. 스트리밍 + TTFT          — answer 가 토큰 단위로 흘러나오는가
5. 거절 오탐                — 보이스피싱 대응 질의가 거절되는가 ★
6. effort 비교              — medium vs low 의 numbers_used 누락률·지연
7. 실측 단가                — 질의 1건당 비용
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm.anthropic_client import AnthropicClient  # noqa: E402
from app.llm.base import GenerationRefused  # noqa: E402
from app.llm.prompts import system_prompt, user_message  # noqa: E402
from app.rag.context import build_context  # noqa: E402
from app.rag.retrieve import get_retriever  # noqa: E402
from app.schemas.common import Lang  # noqa: E402

# sonnet-5 캐시 하한. 미달이면 마커를 붙여도 **에러 없이 조용히** 캐시되지 않는다.
CACHE_MIN_TOKENS = 1024

# 단가 ($/MTok). 2026-08-31 까지 도입가가 적용된다.
PRICING = {
    "claude-sonnet-5": {"in": 3.00, "out": 15.00, "in_intro": 2.00, "out_intro": 10.00},
    "claude-opus-5": {"in": 5.00, "out": 25.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}

# 거절 오탐 확인용. sonnet-5 는 사이버 보안 판정이 강화되어 있고,
# **보이스피싱 대응 안내가 정확히 오탐 위험 구간**이다.
SCAM_QUERIES = [
    "보이스피싱 피해를 입으면 어떤 절차를 밟아야 하나요?",
    "모르는 번호로 검찰이라며 전화가 왔습니다. 일반적으로 어떤 수법인가요?",
    "계좌가 사기에 이용됐다는 연락을 받았습니다. 지급정지는 어떻게 신청하나요?",
    "대출을 미끼로 돈을 요구하는 연락은 어떤 유형인가요?",
    "How do I report financial fraud in Korea?",
]

REAL_QUERY = "한도제한계좌 이체 한도가 왜 100만원인가요"


@dataclass
class Check:
    name: str
    passed: bool | None = None   # None = 판정 불가
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = {True: "✓", False: "✗", None: "—"}[self.passed]
        out = [f"  {mark} {self.name:34} {self.detail}"]
        out += [f"      {n}" for n in self.notes]
        return "\n".join(out)


def cost_usd(model: str, tin: int, tout: int, cache_read: int = 0,
             cache_write: int = 0, intro: bool = True) -> float:
    p = PRICING.get(model, PRICING["claude-sonnet-5"])
    rate_in = p.get("in_intro" if intro else "in", p["in"])
    rate_out = p.get("out_intro" if intro else "out", p["out"])
    return (
        tin * rate_in
        + cache_read * rate_in * 0.1      # 캐시 읽기 ~0.1배
        + cache_write * rate_in * 1.25    # 캐시 쓰기 ~1.25배
        + tout * rate_out
    ) / 1_000_000


async def build_real_prompt(lang: Lang = Lang.KO) -> tuple[str, str]:
    """실제 검색 결과로 프롬프트를 만든다. 합성 프롬프트로는 토큰 수가 안 맞는다."""
    result = get_retriever().search(REAL_QUERY)
    ctx = build_context(result)
    return system_prompt(lang), user_message(question=REAL_QUERY,
                                             context_block=ctx.block, visa="E-9")


# ── 1. 시스템 프롬프트 토큰 수 ───────────────────────────────────────

async def check_prompt_tokens(client: AnthropicClient) -> Check:
    s = get_settings()
    chk = Check("1. 시스템 프롬프트 토큰 수")
    counts: dict[str, int] = {}
    for lang in Lang:
        r = await client._client.messages.count_tokens(
            model=s.llm_model,
            system=[{"type": "text", "text": system_prompt(lang)}],
            messages=[{"role": "user", "content": "x"}],
        )
        counts[lang.value] = r.input_tokens

    worst = min(counts.values())
    chk.passed = worst >= CACHE_MIN_TOKENS
    chk.detail = " / ".join(f"{k} {v:,}" for k, v in counts.items())
    chk.notes.append(f"하한 {CACHE_MIN_TOKENS:,} · 최소 {worst:,}")
    if not chk.passed:
        chk.notes.append("★ 하한 미달 — 캐시가 조용히 비활성화된다. "
                         "시스템 프롬프트에 계층 판정 예시를 추가할 것")
    return chk


# ── 2. 프롬프트 캐시 적중 ────────────────────────────────────────────

async def check_cache(client: AnthropicClient) -> Check:
    chk = Check("2. 프롬프트 캐시 적중")
    system, user = await build_real_prompt()

    stats = []
    for _ in range(2):
        async for ev in client.stream(system=system, user=user, lang=Lang.KO):
            if ev.kind == "final":
                stats.append(ev.result.stats)

    if len(stats) < 2:
        chk.passed = None
        chk.detail = "호출 실패"
        return chk

    first, second = stats
    chk.passed = second.cache_read_tokens > 0
    chk.detail = (f"1회차 write {first.cache_write_tokens:,} / "
                  f"2회차 read {second.cache_read_tokens:,}")
    if not chk.passed:
        chk.notes.append("★ 캐시 미적중 — 시스템 프롬프트에 변동 값(날짜·ID)이 "
                         "섞였거나 하한 미달")
    else:
        saved = cost_usd(first.model, 0, 0, cache_read=second.cache_read_tokens)
        full = cost_usd(first.model, second.cache_read_tokens, 0)
        chk.notes.append(f"절감 {(1 - saved / full) * 100:.0f}% (캐시 구간 기준)")
    return chk


# ── 3·4. 구조화 출력 + 스트리밍 ──────────────────────────────────────

async def check_structured_and_stream(client: AnthropicClient, n: int = 10) -> list[Check]:
    parse = Check(f"3. 구조화 출력 파싱 ({n}회)")
    stream = Check("4. 스트리밍 + TTFT")

    system, user = await build_real_prompt()
    ok = 0
    ttfts: list[float] = []
    token_counts: list[int] = []
    samples: list[str] = []

    for _ in range(n):
        t0 = time.perf_counter()
        ttft = None
        tokens = 0
        try:
            async for ev in client.stream(system=system, user=user, lang=Lang.KO):
                if ev.kind == "token":
                    tokens += 1
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                elif ev.kind == "final":
                    ok += 1
                    if len(samples) < 2:
                        samples.append(ev.result.answer.answer[:70])
        except Exception as e:
            parse.notes.append(f"실패: {type(e).__name__}: {str(e)[:60]}")
        if ttft is not None:
            ttfts.append(ttft)
        token_counts.append(tokens)

    parse.passed = ok == n
    parse.detail = f"{ok}/{n} 검증 통과"

    if ttfts:
        stream.passed = min(token_counts) > 1  # 한 덩어리로 오면 스트리밍이 아니다
        stream.detail = (f"TTFT 평균 {sum(ttfts)/len(ttfts):.2f}s "
                         f"(최소 {min(ttfts):.2f} / 최대 {max(ttfts):.2f}) · "
                         f"조각 {min(token_counts)}~{max(token_counts)}개")
        stream.notes.append("목표 TTFT ≤ 1.8s (planner §14.2)")
        if max(ttfts) > 1.8:
            stream.notes.append("★ TTFT 초과 — effort 를 낮추거나 max_tokens 조정")
    else:
        stream.passed = False
        stream.detail = "토큰 이벤트가 하나도 오지 않음"

    for sm in samples:
        parse.notes.append(f'답변 예: "{sm}…"')
    return [parse, stream]


# ── 5. 거절 오탐 ★ ───────────────────────────────────────────────────

async def check_refusal(client: AnthropicClient) -> Check:
    chk = Check("5. 거절 오탐 (보이스피싱 5종)")
    system, _ = await build_real_prompt()
    refused: list[str] = []

    for q in SCAM_QUERIES:
        result = get_retriever().search(q)
        ctx = build_context(result)
        user = user_message(question=q, context_block=ctx.block)
        try:
            async for ev in client.stream(system=system, user=user, lang=Lang.KO):
                pass
        except GenerationRefused as e:
            refused.append(f"{q[:34]}… (category={e.category})")
        except Exception:
            pass  # 다른 실패는 여기 관심사가 아니다

    chk.passed = not refused
    chk.detail = f"{len(refused)}/{len(SCAM_QUERIES)} 거절"
    for r in refused:
        chk.notes.append(f"거절: {r}")
    if refused:
        chk.notes.append("★ 대응 순서: ①프롬프트에 보호 목적 명시 "
                         "②opus-5 폴백 확인 ③F8 정적 체크리스트로만 처리")
    return chk


# ── 6. effort 비교 ───────────────────────────────────────────────────

async def check_effort(client: AnthropicClient, n: int = 3) -> Check:
    """low 로 내리면 numbers_used 나열이 누락되는지 본다.

    누락되면 숫자 대조 검사가 통과는 하되 **아무것도 잡지 못하는** 상태가 된다.
    """
    chk = Check(f"6. effort 비교 (각 {n}회)")
    s = get_settings()
    system, user = await build_real_prompt()
    rows = []

    for effort in ("low", "medium"):
        object.__setattr__(s, "llm_effort", effort)
        lat, nums, outs = [], [], []
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                async for ev in client.stream(system=system, user=user, lang=Lang.KO):
                    if ev.kind == "final":
                        lat.append(time.perf_counter() - t0)
                        nums.append(len(ev.result.answer.numbers_used))
                        outs.append(ev.result.stats.output_tokens)
            except Exception:
                pass
        if lat:
            rows.append((effort, sum(lat)/len(lat), sum(nums)/len(nums),
                         sum(outs)//len(outs)))

    object.__setattr__(s, "llm_effort", "medium")  # 원복

    if len(rows) < 2:
        chk.passed = None
        chk.detail = "비교 불가"
        return chk

    for effort, lat, nums, outs in rows:
        chk.notes.append(f"{effort:7} 지연 {lat:.2f}s · numbers_used {nums:.1f}개 · "
                         f"출력 {outs}토큰")
    low, med = rows[0], rows[1]
    # numbers_used 가 medium 대비 크게 줄면 low 는 위험하다
    chk.passed = low[2] >= med[2] * 0.8
    chk.detail = "low 사용 가능" if chk.passed else "low 는 numbers_used 누락 위험"
    if not chk.passed:
        chk.notes.append("★ low 에서 수치 나열이 줄었다 — medium 유지 권장")
    return chk


# ── 7. 실측 단가 ─────────────────────────────────────────────────────

async def check_cost(client: AnthropicClient) -> Check:
    chk = Check("7. 실측 단가 (질의 1건)")
    system, user = await build_real_prompt()
    st = None
    async for ev in client.stream(system=system, user=user, lang=Lang.KO):
        if ev.kind == "final":
            st = ev.result.stats
    if st is None:
        chk.passed = None
        chk.detail = "측정 실패"
        return chk

    intro = cost_usd(st.model, st.input_tokens, st.output_tokens,
                     st.cache_read_tokens, st.cache_write_tokens, intro=True)
    full = cost_usd(st.model, st.input_tokens, st.output_tokens,
                    st.cache_read_tokens, st.cache_write_tokens, intro=False)
    chk.passed = True
    chk.detail = f"${intro:.5f} (도입가) / ${full:.5f} (정가)"
    chk.notes.append(f"입력 {st.input_tokens:,} · 출력 {st.output_tokens:,} · "
                     f"캐시읽기 {st.cache_read_tokens:,}")
    chk.notes.append(f"골든셋 100문항 1회 채점 ≈ ${intro * 100:.2f} / "
                     f"240문항 ≈ ${intro * 240:.2f}")
    return chk


# ── 실행 ─────────────────────────────────────────────────────────────

CHECKS = {
    "tokens": check_prompt_tokens,
    "cache": check_cache,
    "structured": check_structured_and_stream,
    "refusal": check_refusal,
    "effort": check_effort,
    "cost": check_cost,
}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=list(CHECKS), help="한 항목만 실행")
    ap.add_argument("--json", type=Path, help="결과를 JSON 으로 저장")
    args = ap.parse_args()

    s = get_settings()
    if not s.llm_enabled:
        print("★ ANTHROPIC_API_KEY 가 설정되지 않았습니다.")
        print("  .env 의 `ANTHROPIC_API_KEY=` 줄에 키를 넣고 다시 실행하세요.")
        return 1

    print(f"모델 {s.llm_model} (폴백 {s.llm_model_fallback}) · effort {s.llm_effort}")
    print(f"임베딩 {s.embed_model}\n")

    client = AnthropicClient()
    get_retriever().search("워밍업")  # 임베딩 모델 선로드 — 지연 측정 오염 방지

    names = [args.only] if args.only else list(CHECKS)
    results: list[Check] = []
    for name in names:
        out = await CHECKS[name](client)
        results.extend(out if isinstance(out, list) else [out])

    print("\n".join(c.render() for c in results))

    failed = [c for c in results if c.passed is False]
    unknown = [c for c in results if c.passed is None]
    print(f"\n{len(results) - len(failed) - len(unknown)}건 통과 · "
          f"{len(failed)}건 실패 · {len(unknown)}건 판정불가")

    if args.json:
        args.json.write_text(json.dumps(
            [{"name": c.name, "passed": c.passed, "detail": c.detail, "notes": c.notes}
             for c in results], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"→ {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

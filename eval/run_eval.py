"""골든셋 자동 채점 (planner §13).

`app.pipeline.ChatPipeline` 을 **그대로** 호출한다. HTTP 를 거치지 않으므로
서버를 띄울 필요가 없고, 라우터를 고쳐도 채점 결과가 흔들리지 않는다.
파이프라인을 HTTP 에서 분리해 둔 이유가 이것이다.

사용법
------
    python eval/run_eval.py --validate-only          # 골든셋 스키마만 검사
    python eval/run_eval.py --no-llm                 # 검색·판정만 (키 불필요)
    python eval/run_eval.py --out eval/reports/exp_001_baseline.json
    python eval/run_eval.py --lang vi --category tier_c_trap   # 일부만

`--no-llm` 이 중요하다. 코퍼스나 임계값을 만질 때마다 **비용 없이** 회귀를
볼 수 있어야 한다. 이 모드에서는 생성이 필요한 지표(사실 포함률·숫자 정확도)가
`None` 으로 남는다 — 0 이 아니라 None 이다. 측정하지 않은 것과 나쁜 것은 다르다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import Outcome, by_group, score, summary_line, trap_breakdown, usage  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden" / "questions.jsonl"

REQUIRED = ("qid", "lang", "category", "question", "expected_tier", "gold_doc_ids")
VALID_TIERS = {"A", "B", "C"}
VALID_LANGS = {"ko", "en", "vi"}


def load_golden(path: Path = GOLDEN) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{n} JSON 오류 — {e}") from e
    return rows


def validate(rows: list[dict], corpus_docs: set[str] | None) -> list[str]:
    """스키마 검사. **채점 전에 반드시 통과해야 한다.**

    골든셋이 조용히 틀어지면 지표가 조용히 틀어진다. 특히 `gold_doc_ids` 가
    코퍼스에 없는 문서를 가리키면 Recall 이 영원히 오르지 않는데, 원인은
    검색이 아니라 골든셋이다.
    """
    errs: list[str] = []
    seen: set[str] = set()
    for r in rows:
        qid = r.get("qid", "<qid 없음>")
        for f in REQUIRED:
            if f not in r:
                errs.append(f"{qid}: 필수 항목 없음 — {f}")
        if qid in seen:
            errs.append(f"{qid}: qid 중복")
        seen.add(qid)
        if r.get("lang") not in VALID_LANGS:
            errs.append(f"{qid}: lang={r.get('lang')!r}")
        if r.get("expected_tier") not in VALID_TIERS:
            errs.append(f"{qid}: expected_tier={r.get('expected_tier')!r}")
        if not (r.get("question") or "").strip():
            errs.append(f"{qid}: question 이 비었음")
        if corpus_docs is not None:
            for doc in r.get("gold_doc_ids", []):
                if doc not in corpus_docs:
                    errs.append(f"{qid}: 코퍼스에 없는 gold_doc_id — {doc}")
        if r.get("category") in ("no_evidence", "tier_c_trap") and r.get("gold_doc_ids"):
            errs.append(f"{qid}: 폴백 대상 문항에 gold_doc_ids 가 있음")
    return errs


def corpus_doc_ids() -> set[str] | None:
    path = REPO_ROOT / "corpus" / "chunks.jsonl"
    if not path.exists():
        return None
    return {json.loads(l)["doc_id"] for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


async def run_one(pipeline, row: dict) -> Outcome:
    from app.schemas.chat import ChatRequest

    out = Outcome(
        qid=row["qid"], lang=row["lang"], category=row["category"],
        expected_tier=row["expected_tier"], expected_fallback=row.get("expected_fallback"),
        gold_doc_ids=row.get("gold_doc_ids", []), gold_facts=row.get("gold_facts", []),
        forbidden_facts=row.get("forbidden_facts", []), gold_numbers=row.get("gold_numbers", []),
    )
    try:
        import time

        req = ChatRequest(message=row["question"], lang=row["lang"], visa=row.get("visa"))
        started = time.perf_counter()
        async for ev in pipeline.run(req):
            if ev.kind == "token" and out.ttft_ms is None:
                out.ttft_ms = int((time.perf_counter() - started) * 1000)
            if ev.chunk_ids:
                out.retrieved_chunk_ids = ev.chunk_ids
            # `meta` 에서만 읽으면 **폴백된 문항의 점수가 전부 0 으로 남는다**
            # (meta 는 임계값 통과 후에만 나간다). 임계값을 튜닝하는데 그
            # 입력이 리포트에 없으면 어느 문항이 간발의 차였는지 알 수 없다.
            if ev.top1:
                out.top1 = ev.top1
            if ev.kind == "final" and ev.response:
                r = ev.response
                out.tier = r.tier.value
                out.fallback_reason = r.fallback_reason.value if r.fallback_reason else None
                out.answer = r.answer
                out.ref_chunk_ids = [ref.chunk_id for ref in r.refs]
                out.unsupported_numbers = r.unsupported_numbers or []
                out.latency_ms = ev.latency_ms
                if ev.stats:
                    out.model = ev.stats.model
                    out.input_tokens = ev.stats.input_tokens
                    out.output_tokens = ev.stats.output_tokens
                    out.cache_read_tokens = ev.stats.cache_read_tokens
                    out.cache_write_tokens = ev.stats.cache_write_tokens
                    out.llm_fallback_used = ev.stats.fallback_used
    except Exception as e:  # noqa: BLE001 — 한 문항의 실패가 채점 전체를 멈추면 안 된다
        out.error = f"{type(e).__name__}: {e}"
    return out


def build_pipeline(no_llm: bool):
    from app.config import get_settings
    from app.llm.base import NullLLMClient
    from app.pipeline import ChatPipeline
    from app.pipeline import build_pipeline as build_serving_pipeline
    from app.rag.retrieve import get_retriever

    s = get_settings()
    if no_llm or not s.llm_enabled:
        if not no_llm:
            print("★ ANTHROPIC_API_KEY 가 없어 --no-llm 으로 진행합니다.\n"
                  "  생성이 필요한 지표는 None 으로 남습니다 (0 이 아닙니다).\n")
        # 번역기도 붙이지 않는다 — `--no-llm` 은 외부 호출을 **한 번도** 하지
        # 않는다는 뜻이고, 질의 정규화(③)도 LLM 호출이다.
        return ChatPipeline(llm=NullLLMClient(), retriever=get_retriever()), True

    # ★ 서버와 **같은 조립**을 쓴다. 여기서 직접 조립하면 `QueryNormalizer` 에
    #   번역기가 빠져, 키가 있어도 ③ 이 passthrough 로 돌아간다. 그러면
    #   다국어 경로를 "번역이 꺼진 상태"로 재게 되는데, 그건 측정하려는
    #   대상이 아니다. 조립이 두 벌이면 언젠가 반드시 어긋난다.
    return build_serving_pipeline(), False


def git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def print_report(report: dict) -> None:
    m = report["metrics"]
    print(f"\n{'지표':22} {'값':>8}  {'모수':>10}  {'목표':>7}")
    print("─" * 58)
    for name, d in m.items():
        if name in ("latency_ms", "ttft_ms"):  # 아래에서 따로 출력한다 (value 키가 없음)
            continue
        val = d["value"]
        shown = "미측정" if val is None else (f"{val:g}건" if name == "forbidden_hits" else f"{val:g}%")
        of = f"{d.get('hit', '')}/{d['of']}" if "of" in d else ""
        tgt = d.get("target")
        tgt_s = "" if tgt is None else (f"≤{tgt:g}" if d.get("lower_is_better") else f"≥{tgt:g}")
        flag = ""
        if val is not None and tgt is not None:
            miss = val > tgt if d.get("lower_is_better") else val < tgt
            flag = " ★" if miss else ""
        print(f"{name:22} {shown:>8}  {of:>10}  {tgt_s:>7}{flag}")
    lat = m["latency_ms"]
    print(f"{'latency p50/p95':22} {str(lat['p50']):>8}  {str(lat['p95']):>10} ms  ≤{lat['target_p95']}")
    if (t := m.get("ttft_ms")) and t["p50"] is not None:
        print(f"{'TTFT p50/p95 ★':22} {str(t['p50']):>8}  {str(t['p95']):>10} ms  "
              f"(체감 지연, n={t['of']})")
    print("\n" + summary_line(report))

    # 과잉 폴백은 **성질이 다른 둘을 섞고 있다.** 총량만 보면 "게이트를 더
    # 풀어야 한다"는 반대 결론으로 간다 — 절반 이상이 환각을 막은 결과다.
    if by_reason := m["over_fallback_rate"].get("by_reason"):
        print("\n과잉 폴백 사유")
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            kind = ("환각을 막은 것 (설계대로)" if reason == "unsupported_number"
                    else "게이트가 과함 (고칠 대상)" if reason == "low_confidence"
                    else "")
            print(f"  {reason:22} {n:3}건  {kind}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="리포트 JSON 경로")
    ap.add_argument("--no-llm", action="store_true", help="검색·판정만 (키 불필요)")
    ap.add_argument("--validate-only", action="store_true", help="스키마만 검사")
    ap.add_argument("--lang", choices=sorted(VALID_LANGS), help="해당 언어만")
    ap.add_argument("--category", help="해당 카테고리만")
    ap.add_argument("--limit", type=int, help="앞에서 N 문항만")
    ap.add_argument("-v", "--verbose", action="store_true", help="파이프라인 로그를 보여준다")
    args = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)

    rows = load_golden()
    docs = corpus_doc_ids()
    if docs is None:
        print("※ corpus/chunks.jsonl 이 없어 gold_doc_ids 대조를 건너뜁니다.")
    if errs := validate(rows, docs):
        print(f"골든셋 오류 {len(errs)}건:")
        for e in errs[:30]:
            print(f"  {e}")
        return 1
    print(f"골든셋 {len(rows)}문항 · 스키마 검사 통과")
    if args.validate_only:
        return 0

    if args.lang:
        rows = [r for r in rows if r["lang"] == args.lang]
    if args.category:
        rows = [r for r in rows if r["category"] == args.category]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("조건에 맞는 문항이 없습니다.")
        return 1

    pipeline, no_llm = build_pipeline(args.no_llm)
    print(f"채점 {len(rows)}문항 (mode={'no-llm' if no_llm else 'llm'})")

    async def run_all() -> list[Outcome]:
        results = []
        for i, row in enumerate(rows, 1):
            results.append(await run_one(pipeline, row))
            if i % 20 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)}")
        return results

    outcomes = asyncio.run(run_all())

    reached_generation = 0
    if no_llm:
        # 생성을 하지 않았으므로 생성 의존 지표는 **측정하지 않은 것**으로 둔다.
        # 빈 답변에 gold_facts 를 대조하면 0% 가 나오는데, 그건 품질이 아니라
        # 실행하지 않은 사실을 잘못 적은 것이다.
        #
        # ★ `upstream_error` 를 폴백으로 세면 안 된다. 키가 없어 생성이 실패한
        #   것은 **판단이 아니라 미실행**이다. 그대로 두면 정상 문항이 전부
        #   "잘못 막힌 것"으로 집계돼 과잉 폴백률이 100% 로 나온다 —
        #   실제로 한 번 그렇게 나왔다. 규칙이 통과시킨 것으로 기록한다.
        for o in outcomes:
            if o.fallback_reason == "upstream_error":
                o.fallback_reason = None
                o.tier = ""
                reached_generation += 1
            o.generated = False
            o.gold_facts, o.forbidden_facts = [], []

    report = score(outcomes)
    evades = {r["qid"]: bool(r.get("evades_regex")) for r in rows}
    report.update({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_rev": git_rev(),
        "mode": "no-llm" if no_llm else "llm",
        "python": platform.python_version(),
        "filters": {"lang": args.lang, "category": args.category, "limit": args.limit},
        "usage": usage(outcomes),
        "by_lang": by_group(outcomes, "lang"),
        "by_category": by_group(outcomes, "category"),
        "trap_breakdown": trap_breakdown(outcomes, evades),
        "failures": [
            {"qid": o.qid, "category": o.category, "lang": o.lang,
             "expected_tier": o.expected_tier, "tier": o.tier,
             "expected_fallback": o.expected_fallback, "fallback": o.fallback_reason,
             "top1": round(o.top1, 4), "error": o.error,
             # ★ 어떤 수치가 거부됐는지 남긴다. 이게 없으면 숫자 대조가
             #   과하게 잡는 것인지 모델이 실제로 지어낸 것인지 리포트만
             #   보고는 영영 가릴 수 없다. 게다가 생성은 실행마다 달라
             #   나중에 재현되지도 않는다.
             "unsupported_numbers": o.unsupported_numbers or None}
            for o in outcomes
            if o.error
            or (o.should_fallback and not o.is_fallback)
            or (not o.should_fallback and o.is_fallback)
        ],
    })

    if no_llm:
        report["reached_generation"] = reached_generation
        print(f"\n※ --no-llm: {reached_generation}문항이 규칙을 통과해 생성 단계까지 갔습니다.")
        print("  생성에 의존하는 지표는 '미측정' 입니다 — 0% 가 아닙니다.")

    print_report(report)

    u = report["usage"]
    if u.get("n_generated"):
        ch = u["cache_hit_rate"]
        print(f"\n사용량 — 생성 {u['n_generated']}건 · 캐시 적중 {ch:g}% "
              f"(읽기 {u['cache_read_tokens']:,} / 쓰기 {u['cache_write_tokens']:,} 토큰) · "
              f"입력 {u['input_tokens']:,} · 출력 {u['output_tokens']:,}")
        if ch == 0:
            print("  ★ 캐시 적중 0% — 시스템 프롬프트가 최소 길이에 미달하면 "
                  "에러 없이 꺼집니다. count_tokens 로 확인하세요.")
        if u["model_fallback_used"]:
            print(f"  ※ 폴백 모델 사용 {u['model_fallback_used']}건")

    tb = report["trap_breakdown"]
    print(f"\n함정 세부 — 정규식이 잡는 표현 {tb['regex_catches']['value']}% "
          f"({tb['regex_catches']['hit']}/{tb['regex_catches']['of']}) · "
          f"우회 표현 {tb['regex_evades']['value']}% "
          f"({tb['regex_evades']['hit']}/{tb['regex_evades']['of']})")

    print("\n언어별 폴백 정확도 / 과잉 폴백률")
    for lang, rep in report["by_lang"].items():
        fa = rep["metrics"]["fallback_accuracy"]["value"]
        of = rep["metrics"]["over_fallback_rate"]["value"]
        print(f"  {lang}  {('—' if fa is None else f'{fa:g}%'):>6}  /  "
              f"{('—' if of is None else f'{of:g}%'):>6}")

    if report["failures"]:
        print(f"\n어긋난 문항 {len(report['failures'])}건 (앞 10건):")
        for f in report["failures"][:10]:
            what = f["error"] or f"기대 {f['expected_fallback'] or f['expected_tier']} → 실제 {f['fallback'] or f['tier']}"
            print(f"  {f['qid']:12} {f['lang']}  {what}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"\n리포트 → {args.out}")
        print("  ★ 리포트는 git 에 커밋합니다. 튜닝 과정의 유일한 정량 증거입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

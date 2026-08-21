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
from dataclasses import asdict
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
    from app.schemas.chat import ChatRequest, SessionContext

    out = Outcome(
        qid=row["qid"], lang=row["lang"], category=row["category"],
        expected_tier=row["expected_tier"], expected_fallback=row.get("expected_fallback"),
        gold_doc_ids=row.get("gold_doc_ids", []), gold_facts=row.get("gold_facts", []),
        forbidden_facts=row.get("forbidden_facts", []), gold_numbers=row.get("gold_numbers", []),
    )
    try:
        import time

        # ★ 비자는 `context.visa` 에 담아야 한다. `ChatRequest(visa=…)` 로 넘기면
        #   그런 필드가 없어 **Pydantic 이 조용히 버린다** — 에러가 나지 않으므로
        #   골든셋 160문항이 전부 `visa` 를 갖고 있는데도 ④ 의 비자 메타 필터와
        #   ③ 의 검색어 비자 결합이 채점에서 한 번도 동작하지 않았다.
        #   E-9/E-7 변별이 이 서비스가 BM25 를 쓰는 이유인데 그 경로가 미측정이었다.
        req = ChatRequest(
            message=row["question"], lang=row["lang"],
            context=SessionContext(visa=row.get("visa")),
        )
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


def backend_info() -> dict:
    """어느 백엔드·모델·장치로 잰 값인지.

    ★ **로컬 모델 수치를 API 기준선과 같은 표에 섞으면 안 된다.** 다른
    시스템을 잰 값이다. 리포트가 스스로 출처를 밝히지 않으면 나중에 파일명만
    보고 비교하게 되고, 그 순간 튜닝 근거가 무너진다.
    """
    from app.config import get_settings

    s = get_settings()
    if s.llm_backend == "local":
        from app.llm.local_client import resolve_device

        return {"backend": "local", "model": s.local_model,
                "device": resolve_device(s.local_device)}
    return {"backend": "anthropic", "model": s.llm_model,
            "effort": s.llm_effort, "thinking": s.llm_thinking}


class NormalizeOnlyClient:
    """③ 는 실제 모델로 돌리고 ⑦ 은 하지 않는다 — `--no-generation`.

    왜 필요한가
    -----------
    **Recall@5 는 ③ 정규화에만 의존한다.** 검색은 정규화된 검색어로 하고 ⑦ 의
    산출물을 쓰지 않는다. 그런데 두 단계의 비용이 자릿수로 다르다 (int8 실측,
    MPS — ADR-005 이전의 폐기된 장치이나 **자릿수 차이는 장치가 바뀌어도
    남는다.** cuda 에서는 절대값이 크게 줄지만 ⑦ 이 ③ 을 압도하는 관계는 같다):

        ③ 정규화   20.8초 × 41문항 ≈ 14분      ← 잴 수 있다
        ⑦ 생성     ~1600초 × 41   ≈ 18시간     ← 못 잰다

    정규화 품질만 보려는데 생성까지 돌리면 **재려는 것의 100배를 기다린다.**
    모델 변형(양자화 등)의 최약 고리가 vi 정규화라면 여기서 먼저 갈린다.

    `--no-llm` 으로는 이걸 못 한다 — `NullLLMClient` 는 **번역기까지** 끄기
    때문에(정규화도 LLM 호출이다) en·vi 질의가 자기 언어 그대로 검색된다.
    그래서 재려는 대상 자체가 사라진다.

    ⚠ 생성 의존 지표는 `--no-llm` 과 똑같이 **미측정**으로 남는다. `stream` 이
      `GenerationFailed` 를 내면 파이프라인이 `UPSTREAM_ERROR` 폴백을 만드는데,
      채점 쪽에서 그것을 폴백으로 세지 않고 되돌린다 — 판단이 아니라 미실행이다.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    async def translate_to_search_terms(self, query: str, lang: str) -> str:
        return await self._inner.translate_to_search_terms(query, lang)

    async def stream(self, *, system, user, lang):
        from app.llm.base import GenerationFailed

        raise GenerationFailed("--no-generation: 생성을 실행하지 않았습니다")
        yield  # pragma: no cover - 시그니처를 제너레이터로 유지


def build_pipeline(no_llm: bool, no_generation: bool = False):
    from app.config import get_settings
    from app.llm.base import NullLLMClient
    from app.pipeline import ChatPipeline
    from app.pipeline import build_pipeline as build_serving_pipeline
    from app.rag.retrieve import get_retriever

    s = get_settings()
    # 로컬 백엔드는 키가 필요 없다. 그 규칙은 `generation_enabled` 하나가 갖는다 —
    # 여기서 백엔드를 다시 분기하면 설정과 평가기가 어긋날 수 있다.
    if no_llm or not s.generation_enabled:
        if not no_llm:
            print("★ ANTHROPIC_API_KEY 가 없어 --no-llm 으로 진행합니다.\n"
                  "  로컬 모델을 쓰려면 LLM_BACKEND=local 로 두세요.\n"
                  "  생성이 필요한 지표는 None 으로 남습니다 (0 이 아닙니다).\n")
        # 번역기도 붙이지 않는다 — `--no-llm` 은 외부 호출을 **한 번도** 하지
        # 않는다는 뜻이고, 질의 정규화(③)도 LLM 호출이다.
        return ChatPipeline(llm=NullLLMClient(), retriever=get_retriever()), True

    # ★ 서버와 **같은 조립**을 쓴다. 여기서 직접 조립하면 `QueryNormalizer` 에
    #   번역기가 빠져, 키가 있어도 ③ 이 passthrough 로 돌아간다. 그러면
    #   다국어 경로를 "번역이 꺼진 상태"로 재게 되는데, 그건 측정하려는
    #   대상이 아니다. 조립이 두 벌이면 언젠가 반드시 어긋난다.
    pipeline = build_serving_pipeline()
    if no_generation:
        # ★ 서버와 같은 조립을 **그대로 쓰고** 생성만 막는다. 여기서 파이프라인을
        #   따로 조립하면 ③ 의 번역기가 빠져 정규화가 passthrough 가 되는데,
        #   그러면 재려던 것이 사라진다 (바로 위 주석과 같은 이유다).
        pipeline._llm = NormalizeOnlyClient(pipeline._llm)  # noqa: SLF001
    return pipeline, False


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
    ap.add_argument("--partial", type=Path,
                    help="문항마다 결과를 JSONL 로 붙여 쓴다 (긴 실행의 중간 저장)")
    ap.add_argument("--resume", type=Path,
                    help="이전 --partial 을 이어받아 남은 문항만 채점한다")
    ap.add_argument("--no-llm", action="store_true", help="검색·판정만 (키 불필요)")
    ap.add_argument("--no-generation", action="store_true",
                    help="③ 정규화는 실제로 돌리고 ⑦ 생성만 건너뛴다 — Recall@5 만 볼 때")
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

    # ★ 이어받기. 로컬 채점은 두 시간이 넘어 중간에 끊기는 일이 실제로 생긴다
    #   (타임아웃·크래시·수동 중단). 이미 채점한 문항을 다시 돌리는 것은 순수한
    #   낭비이므로, `--partial` 로 남긴 결과를 읽어 **남은 문항만** 채점한다.
    resumed: list[Outcome] = []
    if args.resume and args.resume.exists():
        for line in args.resume.read_text(encoding="utf-8").splitlines():
            if line.strip():
                resumed.append(Outcome(**json.loads(line)))
        done = {o.qid for o in resumed}
        before = len(rows)
        rows = [r for r in rows if r["qid"] not in done]
        print(f"이어받기: {len(done)}문항 완료 · 남은 {len(rows)} / 전체 {before}")
        if not rows:
            print("남은 문항이 없습니다 — 이어받은 결과로 리포트만 만듭니다.")


    if args.no_llm and args.no_generation:
        # --no-llm 은 생성도 정규화도 하지 않는다. 함께 주면 --no-generation 이
        # 아무 일도 하지 않으므로, 조용히 넘기지 않고 알린다.
        print("★ --no-llm 과 --no-generation 을 함께 주면 --no-llm 이 이깁니다 "
              "(정규화도 꺼집니다).\n")
        args.no_generation = False

    pipeline, no_llm = build_pipeline(args.no_llm, args.no_generation)
    # 생성을 실행하지 않은 두 모드는 채점 후처리가 같다 — 생성 의존 지표를
    # '미측정'으로 둔다. 다르게 다뤄야 하는 것은 **게이트 비교 가능성**뿐이다.
    skip_gen = no_llm or args.no_generation
    mode = "no-llm" if no_llm else ("no-gen" if args.no_generation else "llm")
    bi = backend_info()
    print(f"채점 {len(rows)}문항 (mode={mode}, "
          f"backend={bi['backend']}, model={bi['model']}"
          + (f", device={bi['device']}" if "device" in bi else "") + ")")

    # ★ 중간 저장. 채점 루프는 **전부 끝난 뒤에만** 리포트를 쓴다. 로컬 모델은
    #   문항당 수십 초라 2시간짜리 실행이 되는데, 중간에 죽으면 전부 잃는다 —
    #   실제로 `exp_006` 은 채점을 다 끝내고 **파일 쓰기 직전에** 죽어 두 번
    #   날렸다. 옵션이 없으면 동작은 이전과 같다.
    partial_fp = None
    if args.partial:
        args.partial.parent.mkdir(parents=True, exist_ok=True)
        partial_fp = args.partial.open("w", encoding="utf-8")
        print(f"중간 저장 → {args.partial}")

    async def run_all() -> list[Outcome]:
        results = []
        for i, row in enumerate(rows, 1):
            outcome = await run_one(pipeline, row)
            results.append(outcome)
            if partial_fp:
                partial_fp.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")
                partial_fp.flush()  # 죽어도 남아 있어야 의미가 있다
            if i % 20 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)}", flush=True)
        return results

    try:
        outcomes = asyncio.run(run_all())
    finally:
        if partial_fp:
            partial_fp.close()

    # 이어받은 결과를 앞에 붙인다. 채점식은 순서에 의존하지 않지만,
    # 골든셋 순서를 유지해야 리포트를 나란히 놓고 읽을 수 있다.
    if resumed:
        order = {r["qid"]: i for i, r in enumerate(load_golden())}
        outcomes = sorted(resumed + outcomes, key=lambda o: order.get(o.qid, 1 << 30))

    reached_generation = 0
    if skip_gen:
        # 생성을 하지 않았으므로 생성 의존 지표는 **측정하지 않은 것**으로 둔다.
        # 빈 답변에 gold_facts 를 대조하면 0% 가 나오는데, 그건 품질이 아니라
        # 실행하지 않은 사실을 잘못 적은 것이다.
        #
        # ★ `upstream_error` 를 폴백으로 세면 안 된다. 키가 없어 생성이 실패한
        #   것은 **판단이 아니라 미실행**이다. 그대로 두면 정상 문항이 전부
        #   "잘못 막힌 것"으로 집계돼 과잉 폴백률이 100% 로 나온다 —
        #   실제로 한 번 그렇게 나왔다. 규칙이 통과시킨 것으로 기록한다.
        #
        # ★★ (`--no-llm` 한정) 같은 이유로 **비한국어의 게이트 지표는 llm 모드와
        #   비교할 수 없다.**
        #   임계값은 질의 번역(③)이 켜진 상태로 보정돼 있는데, `--no-llm` 은
        #   번역도 하지 않는다(번역 역시 LLM 호출이다). 그래서 en·vi 질의가
        #   자기 언어 그대로 검색돼 점수가 낮게 나오고 무더기로 폴백된다 —
        #   실측: 과잉폴백 43.5%, 실패 40건 중 38건이 en·vi.
        #   `--no-llm` 은 **ko 경로와 검색 자체의 회귀**를 보는 도구다.
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
        "mode": mode,
        "backend": backend_info(),
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

    if skip_gen:
        report["reached_generation"] = reached_generation
        # 폴백 관련 지표는 두 모드 모두 비교 불가다 — 생성 단계에서만 생기는
        # 사유(no_citation·unsupported_number)가 통째로 빠지기 때문이다.
        report["gate_metrics_comparable"] = False
        n_non_ko = sum(1 for o in outcomes if o.lang != "ko")
        print(f"\n※ --{'no-llm' if no_llm else 'no-generation'}: "
              f"{reached_generation}문항이 규칙을 통과해 생성 단계까지 갔습니다.")
        print("  생성에 의존하는 지표는 '미측정' 입니다 — 0% 가 아닙니다.")

        if no_llm and n_non_ko:
            print(f"  ★ 비한국어 {n_non_ko}문항의 게이트 지표는 llm 모드와 비교할 수 없습니다.")
            print("    임계값은 질의 번역이 켜진 상태로 보정돼 있는데 --no-llm 은 번역도 끕니다.")
            print("    회귀는 `--lang ko` 로 보거나 llm 모드로 재실행하세요.")
        elif not no_llm:
            # ★ 위의 `--no-llm` 경고가 여기에는 **해당하지 않는다.** 정규화(③)를
            #   실제로 돌렸으므로 검색어가 llm 모드와 같고, 따라서 Recall@5 는
            #   llm 모드 수치와 **직접 비교할 수 있다** — 임계값에도 의존하지
            #   않는다(계획서 §5).
            print("  ★ 정규화(③)는 실제로 돌았습니다 — "
                  "Recall@5 는 llm 모드와 직접 비교 가능합니다.")
            print("    이 모드가 재는 것은 그것 하나입니다. 폴백·계층 지표는 보지 마세요.")

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

    # ★ **언어별로 본다.** 총계는 특정 언어만 망가진 상태를 감춘다
    #   (`metrics.by_group` 주석 참조). 다국어 서비스에서 주 이용자는
    #   비한국어 화자이므로, ko 77문항에 묻힌 vi 실패를 놓치면 안 된다.
    print("\n언어별 (★ 총계보다 이쪽을 먼저 본다)")
    cols = [("언어일치", "answer_language_match", 95.0),
            ("인용", "citation_rate", 100.0),
            ("숫자", "number_accuracy", 98.0),
            ("사실포함", "fact_coverage", 85.0),
            ("폴백정확", "fallback_accuracy", 95.0),
            ("과잉폴백", "over_fallback_rate", 8.0)]
    print("  " + "".join(f"{'':>4}" for _ in range(0)) + "lang " +
          "".join(f"{name:>10}" for name, _, _ in cols))
    for lang, rep in report["by_lang"].items():
        cells = []
        for _, key, target in cols:
            v = rep["metrics"].get(key, {}).get("value")
            if v is None:
                cells.append(f"{'—':>10}")
                continue
            lower_better = key == "over_fallback_rate"
            miss = v > target if lower_better else v < target
            cells.append(f"{f'{v:g}%' + ('★' if miss else ''):>10}")
        print(f"  {lang:5}" + "".join(cells))

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

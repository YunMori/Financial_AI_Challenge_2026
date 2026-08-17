"""언어별 검색 신뢰도 임계값 재보정 (planner §6.6).

    python eval/recalibrate.py eval/reports/exp_008_qwen35-4b_cuda_partial.jsonl

왜 필요한가
-----------
임계값(ko 0.8246 / en 0.8445 / vi 0.8321)은 **haiku 번역이 켜진 상태**로 보정된
값이다. 정규화 모델이 바뀌면 검색어가 바뀌고 점수 분포가 통째로 이동한다.
후보마다 재보정하지 않으면 게이트 의존 지표(과잉폴백·폴백정확·계층일치)는
**모델 품질이 아니라 임계값 부적합**을 재게 된다.

무엇을 기준으로 잡는가
----------------------
게이트가 책임지는 것은 **"근거가 없는 질문" 하나뿐**이다(ADR-001). 계층 C 함정은
주제상 관련이 있어 검색 점수가 높게 나오는 것이 **정상**이고, 그걸 낮은 점수로
만들려고 임계값을 올리면 정상 질의가 같이 죽는다 — 실제로 그렇게 됐다(과잉폴백 33.8%).
그래서 여기서는 **정상 문항 vs `no_evidence` 문항**만 놓고 잰다. `tier_c_trap` 은
표본에서 제외한다.

기준은 functional-spec F5 §6 그대로다: **거짓 폴백(답할 수 있는데 안 함)보다
거짓 생성(근거 없이 답함)을 훨씬 무겁게 취급한다.** 그래서 과잉폴백 상한을 두고
그 안에서 무근거 차단을 최대화하는 값을 고른다.

★ 입력은 **`--partial` JSONL** 이다. 리포트 JSON 의 `failures` 에는 실패 문항의
  top1 만 들어 있어 분포를 다시 그릴 수 없다. 문항별 `top1` 은 partial 에만 있다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

FALLBACK_EXPECTED = frozenset({"no_evidence", "tier_c_trap"})

# 임계값과 무관하게 폴백되는 사유. 재계산에서 이걸 임계값 탓으로 돌리면
# 게이트를 실제보다 과하게 평가한다 — `unsupported_number` 는 환각을 막은 것이다.
THRESHOLD_INDEPENDENT = {"unsupported_number", "no_citation", "phantom_citation",
                         "forbidden_expression", "credential_request",
                         "language_mismatch", "out_of_scope", "tier_c",
                         "scam_verdict", "injection_blocked", "model_refusal"}

# ★ `upstream_error` 는 **판단이 아니라 미실행**이다. `--no-llm` 파셜에서는 생성
#   단계까지 간 문항이 전부 이 사유를 달고 있고(run_eval 이 그걸 리포트 단계에서
#   지우지만 파셜은 그 전에 쓰인다), llm 모드에서도 재시도 소진은 인프라 실패이지
#   게이트의 결정이 아니다. 임계값 재보정에서는 어느 쪽이든 세지 않는다.
NOT_EXECUTED = "upstream_error"


def load(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def require_one_system(rows: list[dict]) -> str:
    """서로 다른 모델의 채점 결과를 한 번에 재보정하지 않는다.

    임계값은 **그 모델의 검색어 분포**에 대한 값이다. 섞으면 어느 쪽에도 맞지 않는
    값이 나온다 — `run_eval.backend_info()` 가 device 까지 기록하는 것과 같은 이유다.
    """
    models = sorted({r.get("model") or "" for r in rows if r.get("generated", True)})
    models = [m for m in models if m]
    if len(models) > 1:
        raise SystemExit(
            "여러 모델의 결과가 섞여 있습니다: " + ", ".join(models) +
            "\n임계값은 모델별 검색어 분포에 대한 값입니다 — 따로 돌리세요."
        )
    return models[0] if models else "(미기록)"


def evaluate(normal: list[dict], none_ev: list[dict], threshold: float) -> tuple[float, float]:
    """(과잉폴백률, 무근거 차단률) — 반드시 **쌍으로** 본다.

    과잉폴백만 보면 "전부 답하는 모델"이 이기고, 무근거 차단만 보면 "전부 거부하는
    모델"이 만점을 받는다(`metrics.py` 의 설계 원칙과 같다).
    """
    over = sum(
        1 for o in normal
        if o.get("top1", 0.0) < threshold
        or (o.get("fallback_reason") in THRESHOLD_INDEPENDENT)
    )
    caught = sum(1 for o in none_ev if o.get("top1", 0.0) < threshold)
    return (100 * over / len(normal) if normal else 0.0,
            100 * caught / len(none_ev) if none_ev else 0.0)


def recalibrate(rows: list[dict], lang: str, max_over: float) -> dict:
    ok = [r for r in rows if r["lang"] == lang and not r.get("error")]
    # 미실행 문항은 표본에서 뺀다. 세지도, 분모에 넣지도 않는다 — 그러지 않으면
    # `--no-llm` 파셜에서 정상 문항이 전부 "잘못 막힌 것"으로 잡힌다.
    ok = [r for r in ok if r.get("fallback_reason") != NOT_EXECUTED]
    normal = [r for r in ok if r["category"] not in FALLBACK_EXPECTED]
    none_ev = [r for r in ok if r["category"] == "no_evidence"]
    if not normal or not none_ev:
        return {"lang": lang, "n_normal": len(normal), "n_none": len(none_ev),
                "threshold": None}

    n_top1 = sorted(r.get("top1", 0.0) for r in normal)
    e_top1 = sorted(r.get("top1", 0.0) for r in none_ev)

    # 후보는 관측된 점수들 사이의 중점. 데이터에 없는 값을 만들지 않는다.
    marks = sorted({*n_top1, *e_top1})
    candidates = [round((a + b) / 2, 6) for a, b in zip(marks, marks[1:])] or marks

    sweep = [(t, *evaluate(normal, none_ev, t)) for t in candidates]
    # 분리 폭. 음수면 **어떤 임계값으로도 두 목표를 동시에 만족할 수 없다** —
    # 임계값의 위치 문제가 아니라 신호의 문제다(ADR-001).
    separation = round(n_top1[0] - e_top1[-1], 4)

    base = {"lang": lang, "n_normal": len(normal), "n_none": len(none_ev),
            "separation": separation, "in_min": n_top1[0], "out_max": e_top1[-1],
            "sweep": sweep}

    # ★ 분리가 안 되면 **값을 추천하지 않는다.** 억지로 하나 고르면 "무근거를 거의
    #   못 막거나 정상 질의를 무더기로 막는" 둘 중 하나인데, 그 값을 `.env` 에 붙일
    #   수 있는 형태로 내놓는 것 자체가 위험하다. 고칠 대상은 임계값이 아니다.
    if separation < 0:
        return {**base, "threshold": None, "reason": "분리 불가"}

    # 과잉폴백 상한 안에서 무근거 차단 최대 → 동률이면 과잉폴백이 낮은 쪽.
    feasible = [s for s in sweep if s[1] <= max_over]
    if not feasible:
        return {**base, "threshold": None, "reason": "상한 내 해 없음"}
    best = max(feasible, key=lambda s: (s[2], -s[1]))
    return {**base, "threshold": best[0], "over_fallback": best[1], "caught": best[2]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("partial", nargs="+", type=Path, help="run_eval --partial JSONL")
    ap.add_argument("--max-over-fallback", type=float, default=8.0,
                    help="과잉폴백 상한 %% (기본 8 — planner 목표치)")
    ap.add_argument("--sweep", action="store_true", help="후보별 전체 표를 찍는다")
    args = ap.parse_args()

    rows = load(args.partial)
    if not rows:
        raise SystemExit("입력이 비었습니다.")
    model = require_one_system(rows)
    skipped = sum(1 for r in rows if r.get("fallback_reason") == NOT_EXECUTED)
    print(f"모델 {model} · 문항 {len(rows)} · 과잉폴백 상한 {args.max_over_fallback:g}%")
    if skipped:
        print(f"  ※ 미실행({NOT_EXECUTED}) {skipped}문항을 표본에서 제외했습니다 — "
              f"판단이 아니라 미실행입니다.")
        if skipped > len(rows) * 0.3:
            print("  ★ 상당수가 미실행입니다. --no-llm 파셜이라면 임계값은 질의 번역이 "
                  "켜진\n    상태로 보정해야 하므로 이 결과를 그대로 쓰면 안 됩니다.")
    print()

    print(f"{'lang':5}{'정상':>5}{'무근거':>6}{'분리폭':>10}{'임계값':>10}"
          f"{'과잉폴백':>9}{'무근거차단':>11}")
    print("─" * 60)
    picked: dict[str, float] = {}
    notes: list[str] = []
    for lang in ("ko", "en", "vi"):
        r = recalibrate(rows, lang, args.max_over_fallback)
        head = f"{lang:5}{r['n_normal']:>5}{r['n_none']:>6}"
        if r["threshold"] is None and "separation" not in r:
            print(f"{head}{'표본 부족 — 재보정 불가':>30}")
            continue
        sep = f"{r['separation']:+.4f}"
        if r["threshold"] is None:
            print(f"{head}{sep:>10}{'—':>10}{'—':>9}{'—':>11}   ★{r['reason']}")
            if r["separation"] < 0:
                notes.append(
                    f"  {lang}: 분리 폭이 음수다 — 근거 있는 질문이 없는 질문보다 낮게 "
                    f"나온다\n     (안 최소 {r['in_min']:.4f} < 밖 최대 "
                    f"{r['out_max']:.4f}). **임계값의 위치 문제가 아니라 신호의 "
                    f"문제다.**\n     고칠 대상은 임계값이 아니라 검색·정규화다 "
                    f"(ADR-001).")
            else:
                notes.append(
                    f"  {lang}: 과잉폴백 {args.max_over_fallback:g}% 안에서 해가 "
                    f"없다. --max-over-fallback 를 올려\n     절충점을 보거나 "
                    f"--sweep 으로 표를 확인한다.")
        else:
            picked[lang] = r["threshold"]
            print(f"{head}{sep:>10}{r['threshold']:>10.4f}"
                  f"{r['over_fallback']:>8.1f}%{r['caught']:>10.1f}%")
        if r["n_none"] < 5:
            notes.append(f"  {lang}: 무근거 표본 {r['n_none']}건 — 신뢰 구간 없는 "
                         f"잠정값이다.")
        if args.sweep:
            print(f"      {'임계값':>10}{'과잉폴백':>9}{'무근거차단':>11}")
            for t, over, caught in r["sweep"]:
                print(f"      {t:>10.4f}{over:>8.1f}%{caught:>10.1f}%")

    if notes:
        print("\n" + "\n".join(notes))

    if picked:
        pairs = ",".join(f"{k}:{v:.4f}" for k, v in picked.items())
        print(f"\n.env 에 붙일 값\n  THRESHOLD_TOP1_BY_LANG={pairs}")
        if len(picked) < 3:
            print(f"  ⚠ {', '.join(sorted({'ko','en','vi'} - set(picked)))} 는 빠져 "
                  f"있다 — 빠진 언어는 `threshold_top1` 기본값으로 떨어진다.")
        print("\n  ★ 이 값은 위 모델의 검색어 분포에 대한 것이다. 모델이나 코퍼스를 "
              "바꾸면\n    반드시 다시 돌린다 — 코퍼스를 늘리면 코퍼스 밖 질의도 "
              "함께 올라간다.")
    else:
        print("\n추천할 임계값이 없다. 위 사유를 먼저 해소한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

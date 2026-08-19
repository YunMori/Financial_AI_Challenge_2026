"""골든셋 리포트 → Plotly 그림 + HTML 보고서 (planner §13).

리포트 JSON 은 튜닝의 정량 증거이지만 **사람이 읽는 형태가 아니다.** planner §13 은
이 파일들을 "발표 자료의 튜닝 과정 슬라이드 원자료 — 개인 참가에서 정량 근거를
보여줄 수 있는 거의 유일한 수단"으로 못박아 두었다. 기획서·발표에 붙이려면 그림이
필요하다.

    python eval/report_charts.py --history                    # exp_001~ 튜닝 과정
    python eval/report_charts.py --compare eval/reports/exp_008_*_cuda.json
    python eval/report_charts.py --scatter <partial>.jsonl     # 신뢰도 분리

★ **다른 시스템을 같은 표에 섞지 않는다.** `run_eval.backend_info()` 가 backend·
model·device 를 리포트에 남기는 이유가 이것이고, 여기서도 같은 규칙을 강제한다:

- `mode`(llm / no-llm)가 다르면 **선을 잇지 않는다.** `--no-llm` 은 번역(③)도 끄므로
  비한국어 게이트 지표가 llm 모드와 비교 불가다(run_eval 의 경고 참조).
- `--compare` 는 입력 리포트의 `backend.device` 가 섞이면 **거부한다.** 장치가
  다르면 dtype·커널이 달라 그리디 디코딩이어도 생성이 갈린다. ADR-005 로
  개발·판정이 cuda 로 통일돼 새 리포트끼리는 이 사고가 나지 않지만,
  **exp_008·009 의 mps 리포트가 남아 있어** 가드는 그대로 둔다.

★ 산점도만 `--partial` JSONL 을 받는다. 리포트 JSON 의 `failures` 에는 **실패 문항의
top1 만** 들어 있어 분포를 그릴 수 없다. 문항별 `top1` 은 partial JSONL 에만 있다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import plotly.graph_objects as go

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS = REPO_ROOT / "eval" / "reports"

# ── 팔레트 ────────────────────────────────────────────────────────────
#
# dataviz 스킬의 검증기(`validate_palette.js`)를 통과한 값이다.
#   3계열 all-pairs(그룹 막대·산점도): CVD ΔE 9.2 / 정상시야 24.0
#   4계열 adjacent(선):               CVD ΔE 9.1 / 정상시야 22.9
# 색을 바꾸려면 검증기를 다시 돌린다 — 눈으로 판단하지 않는다.
#
# ⚠ aqua·yellow 는 밝은 배경 대비가 3:1 미만이라 **직접 레이블 + 표를 반드시 함께
#   낸다**(검증기의 relief 규칙). 아래 모든 그림이 그 둘을 지킨다.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # 파랑·주황·아쿠아·노랑
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
GOOD = "#0ca30c"

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# 밝은 모드로 **의도적으로 고정**한다. 산출물은 기획서에 붙일 PNG 이므로 인쇄·문서가
# 대상 매체다. 반쪽짜리 다크 모드(계열색은 그대로, 배경만 반전)보다 낫다.


def _layout(title: str, ylab: str, right: int = 210, **kw) -> dict:
    """`right` 는 직접 레이블이 넘어갈 여백. 선 그래프만 넓게 잡는다.

    제목 `x` 는 컨테이너 기준이라 0 으로 두면 **글자가 왼쪽에서 잘린다.**
    """
    return dict(
        title=dict(text=title, font=dict(size=17, color=INK), x=0.02, xanchor="left"),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=13, color=INK_2),
        yaxis=dict(title=dict(text=ylab, font=dict(size=12, color=MUTED)),
                   gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS,
                   tickfont=dict(color=MUTED, size=12)),
        xaxis=dict(gridcolor="rgba(0,0,0,0)", linecolor=AXIS,
                   tickfont=dict(color=MUTED, size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(size=12, color=INK_2), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=64, r=right, t=84, b=52),
        hovermode="x unified",
        **kw,
    )


def _target_line(fig: go.Figure, y: float, label: str) -> None:
    """목표선. 회색 파선 — 데이터보다 뒤로 물러나야 한다.

    주석은 **왼쪽**에 붙인다. 오른쪽은 계열의 직접 레이블 자리이고, 둘이 겹치면
    둘 다 못 읽는다.
    """
    fig.add_hline(y=y, line=dict(color=AXIS, width=1, dash="dash"),
                  annotation_text=label, annotation_position="top left",
                  annotation_font=dict(size=11, color=MUTED))


# ── 리포트 로딩 ───────────────────────────────────────────────────────

def load_reports(paths: list[Path]) -> list[dict]:
    out = []
    for p in sorted(paths):
        d = json.loads(p.read_text(encoding="utf-8"))
        d["_name"] = p.stem.replace("exp_", "").replace("_", " ")
        d["_path"] = p
        out.append(d)
    return out


def _val(rep: dict, key: str):
    return rep.get("metrics", {}).get(key, {}).get("value")


def require_same_device(reports: list[dict]) -> None:
    """입력이 같은 시스템에서 나왔는지 확인한다.

    장치가 다르면 dtype·커널이 달라 그리디여도 토큰이 갈릴 수 있어 한 표에
    못 놓는다 — 폐기된 mps 리포트(exp_008·009, fp16)와 cuda 리포트(bf16)가
    대표적이다. backend 를 기록하지 않던 옛 리포트(exp_001~005)는 판정 대상에서
    제외한다.
    """
    seen = {}
    for r in reports:
        b = r.get("backend") or {}
        if not b:
            continue
        key = (b.get("backend"), b.get("device"))
        seen.setdefault(key, []).append(r["_name"])
    if len(seen) > 1:
        lines = "\n".join(f"  {k}: {', '.join(v)}" for k, v in seen.items())
        raise SystemExit(
            "backend/device 가 섞인 리포트는 함께 그릴 수 없습니다.\n"
            f"{lines}\n"
            "장치가 다르면 dtype·커널이 달라 생성이 갈립니다 — 따로 그리세요.
"
            "(mps 리포트는 ADR-005 이전 기록입니다)"
        )


# ── 그림 1: 튜닝 과정 시계열 ──────────────────────────────────────────

METRIC_LINES = [
    ("recall_at_5", "Recall@5", 92.0),
    ("fallback_accuracy", "폴백 정확도", 95.0),
    ("tier_match", "계층 일치율", 90.0),
    ("over_fallback_rate", "과잉 폴백률 (낮을수록 좋음)", 8.0),
]


def fig_history(reports: list[dict]) -> go.Figure:
    """실험별 지표 추이. **무엇을 고쳐서 무엇이 나아졌나.**"""
    names = [r["_name"] for r in reports]
    fig = go.Figure()
    # 끝점이 가까운 계열끼리 레이블이 겹친다(Recall@5 94.6 vs 폴백정확 92.6).
    # 겹치는 쌍만 세로로 벌린다 — 전부 흩뜨리면 어느 선의 레이블인지 흐려진다.
    ends = [(_val(reports[-1], k) or 0) for k, _, _ in METRIC_LINES]
    shifts = [0.0] * len(ends)
    order = sorted(range(len(ends)), key=lambda i: -ends[i])
    for a, b in zip(order, order[1:]):
        if abs(ends[a] - ends[b]) < 6:          # 6%p 안쪽이면 충돌한다
            shifts[a], shifts[b] = 9.0, -9.0

    for i, (key, label, _target) in enumerate(METRIC_LINES):
        ys = [_val(r, key) for r in reports]
        # 마지막 점에 직접 레이블 — 대비가 낮은 계열의 relief 조건이다.
        # 범례에 이미 전체 이름이 있으므로 여기서는 짧은 이름 + 값만 쓴다.
        text = [None] * len(ys)
        last = next((j for j in range(len(ys) - 1, -1, -1) if ys[j] is not None), None)
        if last is not None:
            text[last] = f"  {label.split(' (')[0]} {ys[last]:g}%"
        fig.add_trace(go.Scatter(
            x=names, y=ys, name=label, mode="lines+markers+text",
            text=text, textposition="middle right",
            textfont=dict(size=12, color=INK_2),
            cliponaxis=False,                    # 여백으로 넘어가도 자르지 않는다
            line=dict(color=SERIES[i], width=2),
            marker=dict(size=9, color=SERIES[i],
                        line=dict(color=SURFACE, width=2)),
            connectgaps=False,
        ))
        if shifts[i]:
            fig.data[-1].update(textfont=dict(size=12, color=INK_2))
            fig.data[-1]["textposition"] = "middle right"
            fig.add_annotation(x=names[last], y=ys[last] + shifts[i], xshift=14,
                               text=text[last].strip(), showarrow=False,
                               xanchor="left", font=dict(size=12, color=INK_2))
            fig.data[-1]["text"] = [None] * len(ys)   # 주석으로 대체
    _target_line(fig, 92, "Recall 목표 92")
    fig.update_layout(**_layout(
        "튜닝 과정 — 실험별 지표 추이", "%",
        yaxis_range=[0, 108]))
    return fig


# ── 그림 2: 언어별 Recall@5 ───────────────────────────────────────────

def fig_by_lang(reports: list[dict], key: str = "recall_at_5",
                title: str = "언어별 Recall@5") -> go.Figure:
    """ko/en/vi × 실행. **총계는 특정 언어만 망가진 상태를 감춘다.**"""
    langs = ["ko", "en", "vi"]
    fig = go.Figure()
    for i, r in enumerate(reports):
        ys = [(r.get("by_lang", {}).get(l, {}).get("metrics", {})
               .get(key, {}).get("value")) for l in langs]
        fig.add_trace(go.Bar(
            x=langs, y=ys, name=r["_name"],
            marker=dict(color=SERIES[i % len(SERIES)],
                        line=dict(color=SURFACE, width=2)),  # 2px 표면 간격
            text=[f"{y:g}%" if y is not None else "—" for y in ys],
            textposition="outside",
            textfont=dict(size=11, color=INK_2),
        ))
    _target_line(fig, 92, "목표 92")
    fig.update_layout(**_layout(title, "%", right=40, barmode="group",
                                yaxis_range=[0, 112], bargap=0.28))
    return fig


# ── 그림 3: 과잉 폴백 사유 분해 ───────────────────────────────────────

REASON_LABEL = {
    "low_confidence": "low_confidence — 게이트가 과함 (고칠 대상)",
    "unsupported_number": "unsupported_number — 환각을 막은 것 (설계대로)",
}


def fig_over_fallback(reports: list[dict]) -> go.Figure | None:
    """총량만 보면 "게이트를 더 풀어야 한다"는 **반대 결론**으로 간다.

    ★ `by_reason` 은 `metrics.py` 에 **나중에 추가된 필드**라 옛 리포트에는 없다.
      데이터가 없으면 **빈 그림을 내지 않고 None 을 돌려준다** — 축만 있는 그림은
      "0건"으로 읽혀서, 이 프로젝트가 `_pct()` 에서 지키는 "미측정 ≠ 0" 원칙을
      그림에서 깨뜨린다.
    """
    names = [r["_name"] for r in reports]
    by_reason = [r.get("metrics", {}).get("over_fallback_rate", {})
                 .get("by_reason", {}) or {} for r in reports]
    reasons = sorted({k for d in by_reason for k in d},
                     key=lambda k: (k != "low_confidence", k))
    if not reasons:
        return None
    fig = go.Figure()
    for i, reason in enumerate(reasons):
        ys = [d.get(reason, 0) for d in by_reason]
        fig.add_trace(go.Bar(
            x=names, y=ys, name=REASON_LABEL.get(reason, reason),
            marker=dict(color=SERIES[i % len(SERIES)],
                        line=dict(color=SURFACE, width=2)),
            text=[str(y) if y else "" for y in ys],
            textposition="inside",
            textfont=dict(size=11, color=SURFACE),
        ))
    fig.update_layout(**_layout(
        "과잉 폴백 사유 분해 — 성질이 다른 둘을 섞고 있다", "건수",
        right=40, barmode="stack", bargap=0.4))
    return fig


MISSING_BY_REASON = (
    "<div class='note'><b>과잉 폴백 사유 분해는 그리지 않았다.</b> "
    "<code>by_reason</code> 는 <code>eval/metrics.py</code> 에 나중에 추가된 필드라 "
    "여기 실린 리포트에는 들어 있지 않다. 총량(과잉 폴백률)만으로는 "
    "<code>low_confidence</code>(게이트가 과함 — 고칠 대상)와 "
    "<code>unsupported_number</code>(환각을 막은 것 — 설계대로)를 가를 수 없어, "
    "빈 그림 대신 이 설명을 둔다. 다음 실행부터 자동으로 그려진다.</div>"
)


# ── 그림 4: 신뢰도 분리 산점도 (partial JSONL) ────────────────────────

def fig_separation(partial: Path) -> go.Figure:
    """정상 문항 vs 무근거 문항의 top1 분포.

    **게이트가 실제로 분리하는지를 눈으로 증명한다** — 기획서에서 값이 가장 큰 그림.
    리포트 JSON 에는 실패 문항의 top1 만 있어 이 그림은 partial JSONL 이 필요하다.
    """
    rows = [json.loads(l) for l in partial.read_text(encoding="utf-8").splitlines() if l.strip()]
    groups = [
        ("정상 (근거 있음)", [r for r in rows if r["category"] not in
                              ("no_evidence", "tier_c_trap")], SERIES[0]),
        ("무근거 (no_evidence)", [r for r in rows if r["category"] == "no_evidence"],
         SERIES[1]),
    ]
    fig = go.Figure()
    for label, rs, color in groups:
        fig.add_trace(go.Box(
            x=[r["lang"] for r in rs], y=[r.get("top1", 0) for r in rs],
            name=label, boxpoints="all", jitter=0.4, pointpos=0,
            marker=dict(color=color, size=7,
                        line=dict(color=SURFACE, width=1)),
            line=dict(color=color, width=2),
            fillcolor="rgba(0,0,0,0)",
        ))
    fig.update_layout(**_layout(
        "검색 신뢰도 분리 — 게이트가 무엇을 가르는가", "top1 (코사인 유사도)",
        boxmode="group"))
    return fig


# ── 표 (대비 relief + 원자료) ─────────────────────────────────────────

TABLE_ROWS = [
    ("근거 인용률", "citation_rate", "≥100"),
    ("Recall@5", "recall_at_5", "≥92"),
    ("폴백 정확도", "fallback_accuracy", "≥95"),
    ("과잉 폴백률", "over_fallback_rate", "≤8"),
    ("폴백 사유 일치", "fallback_reason_match", "≥90"),
    ("사실 포함률", "fact_coverage", "≥90"),
    ("출력 언어 일치", "answer_language_match", "≥95"),
    ("숫자 정확도", "number_accuracy", "≥98"),
    ("계층 일치율", "tier_match", "≥90"),
]


def table_html(reports: list[dict]) -> str:
    head = "".join(f"<th>{r['_name']}</th>" for r in reports)
    body = []
    for label, key, target in TABLE_ROWS:
        cells = []
        for r in reports:
            v = _val(r, key)
            cells.append("<td class='n'>—</td>" if v is None
                         else f"<td class='n'>{v:g}%</td>")
        body.append(f"<tr><th scope='row'>{label}</th>"
                    f"<td class='t'>{target}</td>{''.join(cells)}</tr>")
    lat = []
    for r in reports:
        m = r.get("metrics", {}).get("latency_ms", {})
        lat.append(f"<td class='n'>{m.get('p50')} / {m.get('p95')}</td>")
    body.append(f"<tr><th scope='row'>지연 p50/p95 (ms)</th>"
                f"<td class='t'>p95≤6000</td>{''.join(lat)}</tr>")
    return (f"<table><thead><tr><th>지표</th><th>목표</th>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


# ── HTML 보고서 ───────────────────────────────────────────────────────

CSS = f"""
:root {{ color-scheme: light; }}
body {{ margin:0; background:#f9f9f7; color:{INK};
        font-family:{FONT}; line-height:1.6; }}
main {{ max-width:1000px; margin:0 auto; padding:48px 24px 80px; }}
h1 {{ font-size:28px; margin:0 0 6px; letter-spacing:-.01em; }}
h2 {{ font-size:19px; margin:44px 0 10px; }}
.sub {{ color:{INK_2}; margin:0 0 8px; }}
.meta {{ color:{MUTED}; font-size:13px; margin:0 0 32px; }}
.note {{ background:{SURFACE}; border:1px solid {GRID}; border-left:3px solid {SERIES[0]};
         border-radius:6px; padding:12px 16px; margin:14px 0; font-size:14px;
         color:{INK_2}; }}
figure {{ margin:0 0 8px; background:{SURFACE}; border:1px solid {GRID};
          border-radius:10px; padding:8px; overflow-x:auto; }}
figcaption {{ color:{MUTED}; font-size:13px; margin:0 0 28px; }}
.tablewrap {{ overflow-x:auto; background:{SURFACE}; border:1px solid {GRID};
              border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ padding:9px 14px; text-align:left; border-bottom:1px solid {GRID};
          white-space:nowrap; }}
thead th {{ color:{MUTED}; font-weight:600; font-size:13px; }}
tbody th {{ font-weight:500; }}
td.n, td.t {{ font-variant-numeric:tabular-nums; text-align:right; }}
td.t {{ color:{MUTED}; }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom:0; }}
"""


def build_html(title: str, subtitle: str, meta: str,
               blocks: list[tuple[go.Figure | str, str]]) -> str:
    parts, first = [], True
    for item, caption in blocks:
        if isinstance(item, str):
            parts.append(item if caption == "raw" else
                         f"<div class='tablewrap'>{item}</div>"
                         f"<figcaption>{caption}</figcaption>")
            continue
        html = item.to_html(full_html=False, include_plotlyjs="inline" if first else False,
                            config={"displayModeBar": False})
        first = False
        parts.append(f"<figure>{html}</figure><figcaption>{caption}</figcaption>")
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{CSS}</style></head><body><main>"
            f"<h1>{title}</h1><p class='sub'>{subtitle}</p>"
            f"<p class='meta'>{meta}</p>{''.join(parts)}</main></body></html>")


def write_png(fig: go.Figure, path: Path) -> None:
    """기획서에 붙일 PNG. kaleido 가 없으면 건너뛴다(HTML 은 그대로 나온다)."""
    try:
        fig.write_image(str(path), width=1000, height=520, scale=2)
    except Exception as e:  # noqa: BLE001
        print(f"  ※ PNG 생략 ({path.name}): {type(e).__name__}")


# ── 진입점 ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--history", action="store_true", help="커밋된 리포트로 튜닝 과정")
    g.add_argument("--compare", nargs="+", type=Path, help="후보 비교 (같은 device 만)")
    g.add_argument("--scatter", type=Path, help="신뢰도 분리 산점도 (partial JSONL)")
    ap.add_argument("--out", type=Path, help="HTML 경로")
    args = ap.parse_args()

    outdir = REPORTS / "charts"
    outdir.mkdir(parents=True, exist_ok=True)

    if args.scatter:
        fig = fig_separation(args.scatter)
        out = args.out or outdir / "separation.html"
        out.write_text(build_html(
            "검색 신뢰도 분리", "정상 문항과 무근거 문항의 top1 분포",
            f"원자료: {args.scatter.name}",
            [(fig, "게이트가 책임지는 것은 '근거 없음' 하나뿐이다 (ADR-001).")],
        ), encoding="utf-8")
        write_png(fig, out.with_suffix(".png"))
        print(f"→ {out}")
        return 0

    if args.compare:
        reports = load_reports(args.compare)
        require_same_device(reports)
        b = reports[0].get("backend") or {}
        meta = (f"backend={b.get('backend','?')} · device={b.get('device','?')} · "
                f"{len(reports)}개 후보")
        over = fig_over_fallback(reports)
        blocks = [
            (fig_by_lang(reports), "vi 가설이 여기서 갈린다 — 최약 고리다."),
            (fig_by_lang(reports, "answer_language_match", "언어별 출력 언어 일치율"),
             "요청 언어로 답했는가. 무너지면 다른 지표는 볼 필요가 없다."),
            (over, "총량이 아니라 사유로 본다.") if over else (MISSING_BY_REASON, "raw"),
            (table_html(reports), "원자료. — 는 미측정(0% 가 아니다)."),
        ]
        title, sub = "후보 모델 비교", "골든셋 160문항 · 같은 device 기준"
        out = args.out or outdir / "compare.html"
    else:
        allr = load_reports(list(REPORTS.glob("exp_*.json")))
        reports = [r for r in allr if r.get("mode") == "llm"]
        if not reports:
            raise SystemExit("llm 모드 리포트가 없습니다.")
        skipped = [r["_name"] for r in allr if r.get("mode") != "llm"]
        meta = (f"llm 모드 {len(reports)}회 · 골든셋 "
                f"{reports[-1].get('n_total','?')}문항 · git {reports[-1].get('git_rev','?')}")
        over = fig_over_fallback(reports)
        blocks = [
            (fig_history(reports), "무엇을 고쳐서 무엇이 나아졌나."),
            (fig_by_lang(reports), "총계는 특정 언어만 망가진 상태를 감춘다."),
            (over, "절반 이상이 환각을 막은 것이다 — 총량만 보면 반대 결론으로 간다.")
            if over else (MISSING_BY_REASON, "raw"),
            (table_html(reports), "원자료. — 는 미측정(0% 가 아니다)."),
        ]
        if skipped:
            blocks.insert(0, (
                f"<div class='note'>no-llm 모드 리포트 {len(skipped)}건"
                f"({', '.join(skipped)})은 <b>제외했다</b>. <code>--no-llm</code> 은 "
                f"질의 번역(③)도 끄므로 비한국어 게이트 지표를 llm 모드와 같은 선에 "
                f"놓을 수 없다.</div>", "raw"))
        title, sub = "튜닝 과정 리포트", "커밋된 골든셋 실험의 정량 추이"
        out = args.out or outdir / "history.html"

    out.write_text(build_html(title, sub, meta, blocks), encoding="utf-8")
    print(f"→ {out}")
    for item, _ in blocks:
        if isinstance(item, go.Figure):
            name = (item.layout.title.text or "fig")[:24].replace(" ", "_").replace("/", "-")
            write_png(item, outdir / f"{out.stem}_{name}.png")
    print(f"  PNG → {outdir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""골든셋 채점식 (planner §13.2).

지표를 `run_eval.py` 에서 분리한 이유는 **계산식을 눈으로 검토할 수 있게**
하기 위해서다. 채점식이 실행 코드에 섞여 있으면 "이 숫자가 무엇을 센 것인가"를
나중에 아무도 확인하지 않는다.

설계 원칙 하나만 기억하면 된다.

    **폴백 정확도와 과잉 폴백률은 반드시 쌍으로 본다.**

폴백 정확도만 관리하면 "전부 거부하는 모델"이 만점을 받는다. 반대로 과잉
폴백률만 보면 "전부 답하는 모델"이 이긴다. 둘을 함께 놓아야 지표가 실제
품질을 가리킨다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from statistics import median

# ── 출력 언어 판정 ────────────────────────────────────────────────────
#
# ★ 이 검사는 **원래 어디에도 없었다.** `postprocess.finalize()` 는 `lang` 을
#   받지만 폴백 문구와 고지 삽입에만 쓴다. 즉 **베트남어 질문에 한국어로 답해도
#   인용·숫자·금지표현 검사를 전부 통과한다.** Claude 는 지시를 따랐으니
#   드러나지 않았을 뿐이고, 소형 모델의 전형적 실패가 바로 언어 이탈이다.
#   `citations` 생략과 같은 부류다 — 모델의 선의에 기대던 자리.
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
# 베트남어 고유자(완성형). 라틴 알파벳만으로는 en 과 구분되지 않는다.
_VIET = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯ]")

# ⚠ "한글이 없어야 한다"로 판정하면 **안 된다.** 시스템 프롬프트가 en·vi 답변에
#   "필요하면 한국어 원어를 괄호로 병기"하도록 지시한다(`app/llm/prompts.py`).
#   정상 답변에도 한글이 섞이므로 **비율**로 본다.
_HANGUL_MAX_FOR_NON_KO = 0.30   # 병기 수준을 넘으면 한국어로 답한 것
_HANGUL_MIN_FOR_KO = 0.20       # 한국어 답변이면 이보다는 한글이 많다


def detect_answer_language(text: str, expected: str) -> bool | None:
    """답변이 요청 언어로 쓰였는가. 판정 불가면 None.

    스크립트 기반이다 — 모델을 더 쓰지 않는다. 채점기가 채점 대상과 같은
    종류의 실패를 하면 지표를 믿을 수 없기 때문이다.
    """
    stripped = "".join(ch for ch in (text or "") if not ch.isspace())
    if len(stripped) < 20:
        return None  # 폴백 문구·빈 답변은 대상이 아니다

    hangul_ratio = len(_HANGUL.findall(stripped)) / len(stripped)
    # 결합 성조가 분해돼 있어도 잡히도록 NFC 로 맞춘다.
    has_viet = bool(_VIET.search(unicodedata.normalize("NFC", text)))

    if expected == "ko":
        return hangul_ratio >= _HANGUL_MIN_FOR_KO
    if expected == "vi":
        return has_viet and hangul_ratio <= _HANGUL_MAX_FOR_NON_KO
    if expected == "en":
        return not has_viet and hangul_ratio <= _HANGUL_MAX_FOR_NON_KO
    return None

# 채점 대상 카테고리 구분.
FALLBACK_EXPECTED = frozenset({"no_evidence", "tier_c_trap"})


@dataclass(slots=True)
class Outcome:
    """문항 하나의 실행 결과. 채점에 필요한 최소한만 담는다."""

    qid: str
    lang: str
    category: str
    expected_tier: str
    expected_fallback: str | None
    gold_doc_ids: list[str]
    gold_facts: list[str]
    forbidden_facts: list[str]
    gold_numbers: list[str]

    # ── 실행 결과 ────────────────────────────────────────────────────
    tier: str = ""
    fallback_reason: str | None = None
    answer: str = ""
    ref_chunk_ids: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    top1: float = 0.0
    latency_ms: int = 0
    # 첫 토큰까지의 시간. **이용자가 실제로 체감하는 지연은 이쪽이다**
    # (planner §14.2 가 별도 지표로 두라고 명시). 총 완료 시간은 고정
    # 오버헤드가 지배해 설정으로 줄지 않는 반면, TTFT 는 스트리밍이 있는 한
    # 짧고 그것이 화면에 글자가 나타나기까지의 시간이다.
    ttft_ms: int | None = None
    error: str | None = None
    # 생성을 실제로 돌렸는가. `--no-llm` 에서는 False 이고, 생성에 의존하는
    # 지표(인용률·숫자 정확도·계층 일치율)는 0% 가 아니라 **None** 이 된다.
    generated: bool = True

    # ── 생성 사용량 ──────────────────────────────────────────────────
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_fallback_used: bool = False

    @property
    def is_fallback(self) -> bool:
        return self.fallback_reason is not None

    @property
    def should_fallback(self) -> bool:
        return self.category in FALLBACK_EXPECTED

    def retrieved_docs(self, top: int | None = None) -> set[str]:
        """청크 ID(`DOC#0000`)에서 문서 ID 를 뽑는다."""
        ids = self.retrieved_chunk_ids[:top] if top else self.retrieved_chunk_ids
        return {c.split("#", 1)[0] for c in ids}


def _pct(hit: int, total: int) -> float | None:
    """모수가 0이면 **0% 가 아니라 None** 이다.

    측정하지 않은 것을 0으로 적으면 리포트를 나중에 읽을 때 "성능이 나빴다"로
    읽힌다. 둘은 전혀 다른 사실이다.
    """
    return round(100 * hit / total, 1) if total else None


def score(outcomes: list[Outcome]) -> dict:
    """9개 지표를 계산한다. 모수도 함께 담아 해석 가능하게 만든다."""
    ok = [o for o in outcomes if o.error is None]
    answered = [o for o in ok if not o.is_fallback and o.generated]
    normal = [o for o in ok if not o.should_fallback]      # 답이 나와야 하는 문항
    traps = [o for o in ok if o.should_fallback]           # 폴백돼야 하는 문항
    with_gold = [o for o in normal if o.gold_doc_ids]

    # ── 근거 인용률 — 폴백을 제외한 응답 중 인용이 1건 이상인 비율
    cited = sum(1 for o in answered if o.ref_chunk_ids)

    # ── Recall@5 — 정답 문서가 상위 5건 안에 있는가
    #    폴백된 문항도 포함한다. 그래야 "검색이 못 찾은 것"과 "찾았는데
    #    임계값이 막은 것"이 갈린다.
    recall_hit = sum(1 for o in with_gold if set(o.gold_doc_ids) & o.retrieved_docs(5))

    # ── 폴백 정확도 / 과잉 폴백률 — **반드시 함께 본다**
    trap_caught = sum(1 for o in traps if o.is_fallback)
    over_fallback = sum(1 for o in normal if o.is_fallback)

    # ★ 과잉 폴백을 사유별로 쪼갠다. 이 수치는 **성질이 다른 둘을 섞고 있다.**
    #
    #   `low_confidence`      게이트가 과했다 — 고쳐야 할 실패
    #   `unsupported_number`  모델이 근거에 없는 수치를 냈고 검사가 막았다
    #                         — **설계대로 동작한 것**이다
    #
    # 실측(exp_004)에서 후자가 8/14 였고, 거부된 값은 코퍼스에 한 번도 없는
    # 연도(2012)였다. 즉 환각을 정확히 막은 건인데 지표에는 실패로 잡힌다.
    # 총량만 보면 "게이트를 더 풀어야 한다"는 반대 결론으로 간다.
    over_by_reason: dict[str, int] = {}
    for o in normal:
        if o.is_fallback:
            over_by_reason[o.fallback_reason or "unknown"] = (
                over_by_reason.get(o.fallback_reason or "unknown", 0) + 1
            )

    # ── 폴백 사유 일치 — 막은 것과 막은 이유가 함께 맞아야 한다
    reason_match = sum(
        1 for o in traps
        if o.is_fallback and o.expected_fallback and o.fallback_reason == o.expected_fallback
    )
    reason_total = sum(1 for o in traps if o.expected_fallback)

    # ── 사실 포함률 — gold_facts 가 답변에 등장하는 비율 (문항 단위 평균)
    # ★ 대소문자를 구분하면 영어에서 문장 첫머리의 "Limit" 이 "limit" 과 다르게
    #   잡혀 정상 답변이 미달로 집계된다. 한국어에는 영향이 없다.
    fact_rates = [
        sum(1 for f in o.gold_facts if f.lower() in o.answer.lower()) / len(o.gold_facts)
        for o in answered if o.gold_facts
    ]

    # ── 금지 사실 — 하나라도 나오면 실패. 비율이 아니라 **건수**로 본다
    forbidden = [
        (o.qid, f) for o in ok for f in o.forbidden_facts if f and f in o.answer
    ]

    # ── 숫자 정확도 — 근거로 뒷받침되지 않은 수치가 없는 응답의 비율
    num_ok = sum(1 for o in answered if not o.unsupported_numbers)

    # ── 출력 언어 일치 — **이게 무너지면 나머지 지표는 볼 필요가 없다**
    #    다국어 서비스에서 요청 언어가 아닌 언어로 답하면, 인용이 맞고 숫자가
    #    맞아도 이용자에게는 읽을 수 없는 답변이다.
    lang_judged = [(o, detect_answer_language(o.answer, o.lang)) for o in answered]
    lang_scored = [(o, v) for o, v in lang_judged if v is not None]
    lang_ok = sum(1 for _, v in lang_scored if v)

    # ── 계층 일치율 — 폴백이 아닌 응답에 대해서만 본다
    #    (폴백은 항상 C 라 섞으면 함정 문항 때문에 값이 부풀려진다)
    tier_match = sum(1 for o in answered if o.tier == o.expected_tier)

    lat = sorted(o.latency_ms for o in ok)
    ttft = sorted(o.ttft_ms for o in ok if o.ttft_ms is not None)
    return {
        "n_total": len(outcomes),
        "n_scored": len(ok),
        "n_error": len(outcomes) - len(ok),
        "metrics": {
            "citation_rate": {
                "value": _pct(cited, len(answered)), "hit": cited, "of": len(answered),
                "target": 100.0, "note": "폴백 제외 응답 중 인용 ≥1",
            },
            "recall_at_5": {
                "value": _pct(recall_hit, len(with_gold)), "hit": recall_hit,
                "of": len(with_gold), "target": 92.0,
                "note": "gold_doc_ids ∩ 상위5 ≠ ∅ (폴백된 문항도 포함)",
            },
            "fallback_accuracy": {
                "value": _pct(trap_caught, len(traps)), "hit": trap_caught,
                "of": len(traps), "target": 95.0,
                "note": "★ 과잉 폴백률과 반드시 함께 볼 것",
            },
            "over_fallback_rate": {
                "value": _pct(over_fallback, len(normal)), "hit": over_fallback,
                "of": len(normal), "target": 8.0, "lower_is_better": True,
                "by_reason": over_by_reason,
                "note": "★ 정상 문항을 잘못 막은 비율. by_reason 을 반드시 함께 볼 것 — "
                        "unsupported_number 는 환각을 막은 것이라 성질이 다르다",
            },
            "fallback_reason_match": {
                "value": _pct(reason_match, reason_total), "hit": reason_match,
                "of": reason_total, "target": 90.0,
                "note": "막은 이유까지 맞았는가",
            },
            "fact_coverage": {
                "value": round(100 * sum(fact_rates) / len(fact_rates), 1) if fact_rates else None,
                "of": len(fact_rates), "target": 90.0,
                "note": "gold_facts 포함 비율의 문항 평균",
            },
            "forbidden_hits": {
                "value": len(forbidden), "target": 0, "lower_is_better": True,
                "items": forbidden[:10], "note": "0이 아니면 무조건 실패",
            },
            "answer_language_match": {
                "value": _pct(lang_ok, len(lang_scored)), "hit": lang_ok,
                "of": len(lang_scored), "target": 95.0,
                "note": "★ 요청 언어로 답했는가. 무너지면 다른 지표는 의미가 없다 "
                        "(짧은 폴백 문구는 판정 대상에서 제외)",
            },
            "number_accuracy": {
                "value": _pct(num_ok, len(answered)), "hit": num_ok, "of": len(answered),
                "target": 98.0, "note": "근거에 없는 수치가 없는 응답 비율",
            },
            "tier_match": {
                "value": _pct(tier_match, len(answered)), "hit": tier_match,
                "of": len(answered), "target": 90.0,
                "note": "폴백 제외 — 폴백은 항상 C 라 섞으면 부풀려진다",
            },
            "latency_ms": {
                "p50": lat[len(lat) // 2] if lat else None,
                "p95": lat[int(len(lat) * 0.95)] if lat else None,
                "target_p95": 6000,
            },
            "ttft_ms": {
                "p50": ttft[len(ttft) // 2] if ttft else None,
                "p95": ttft[int(len(ttft) * 0.95)] if ttft else None,
                "of": len(ttft),
                "note": "첫 토큰까지 — 이용자 체감 지연 (planner §14.2)",
            },
        },
    }


def usage(outcomes: list[Outcome]) -> dict:
    """토큰 사용량과 캐시 적중률.

    **캐시는 시스템 프롬프트가 최소 길이(1,024토큰)에 못 미치면 에러 없이
    꺼진다.** 적중률을 리포트에 남기지 않으면 꺼진 사실을 영영 모른 채
    비용만 몇 배로 낸다. 지표가 아니라 계측이다.
    """
    gen = [o for o in outcomes if o.generated and o.input_tokens]
    if not gen:
        return {"n_generated": 0}
    hit = sum(1 for o in gen if o.cache_read_tokens > 0)
    return {
        "n_generated": len(gen),
        "cache_hit_rate": _pct(hit, len(gen)),
        "input_tokens": sum(o.input_tokens for o in gen),
        "output_tokens": sum(o.output_tokens for o in gen),
        "cache_read_tokens": sum(o.cache_read_tokens for o in gen),
        "cache_write_tokens": sum(o.cache_write_tokens for o in gen),
        "model_fallback_used": sum(1 for o in gen if o.llm_fallback_used),
        "models": sorted({o.model for o in gen if o.model}),
    }


def by_group(outcomes: list[Outcome], key: str) -> dict[str, dict]:
    """언어별·카테고리별로 쪼개 본다.

    전체 평균은 **특정 언어만 망가진 상태를 감춘다.** 실제로 베트남어는
    검색 신뢰도 분리가 되지 않는데(dev-log 2026-08-12), 전체 수치만 보면
    한국어 70문항에 묻혀 보이지 않는다.
    """
    groups: dict[str, list[Outcome]] = {}
    for o in outcomes:
        groups.setdefault(getattr(o, key), []).append(o)
    return {k: score(v) for k, v in sorted(groups.items())}


def trap_breakdown(outcomes: list[Outcome], evades: dict[str, bool]) -> dict:
    """함정을 **정규식이 잡는 것 / 못 잡는 것**으로 나눠 본다.

    이미 정규식이 잡는 표현만으로 골든셋을 채우면 100% 가 나오고 아무것도
    배우지 못한다. 우회 표현을 따로 세야 "규칙이 어디까지 막고, 나머지는
    무엇이 막는가"가 보인다.
    """
    traps = [o for o in outcomes if o.category == "tier_c_trap" and o.error is None]
    out = {}
    for label, want in (("regex_catches", False), ("regex_evades", True)):
        rows = [o for o in traps if evades.get(o.qid) is want]
        caught = sum(1 for o in rows if o.is_fallback)
        out[label] = {"value": _pct(caught, len(rows)), "hit": caught, "of": len(rows)}
    return out


def summary_line(report: dict) -> str:
    """한 줄 요약. 실행할 때마다 눈으로 비교하기 위한 것."""
    m = report["metrics"]

    def v(name: str) -> str:
        x = m[name]["value"]
        return "—" if x is None else f"{x:g}"

    return (
        f"인용 {v('citation_rate')}% · Recall@5 {v('recall_at_5')}% · "
        f"폴백정확 {v('fallback_accuracy')}% · 과잉폴백 {v('over_fallback_rate')}% · "
        f"숫자 {v('number_accuracy')}% · 계층 {v('tier_match')}% · "
        f"금지표현 {m['forbidden_hits']['value']}건"
    )


__all__ = ["Outcome", "score", "by_group", "trap_breakdown", "summary_line", "usage", "median"]

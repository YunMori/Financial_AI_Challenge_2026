"""`fit_score` — 기관 정렬 기준 (planner §9.2, 기획서 14.3 "기준 공개").

**중립성 4원칙**: 제휴·수수료·광고는 입력에 없다. 여기 있는 세 항목이 전부이고,
`GET /institutions/ranking-policy` 가 **이 모듈의 상수를 그대로 읽어** 공개한다.
산식을 두 곳에 적으면 반드시 어긋나므로 문서용 사본을 만들지 않는다.

    fit_score      = 0.5×visa_fit + 0.3×doc_burden + 0.2×channel_access
    visa_fit       = official 1.0 / inferred 0.6 / unknown 0.0
    doc_burden     = 1 - (required_docs 수 / 전체 기관 최대 서류 수)
    channel_access = 언어지원 0.5 + 비대면 0.3 + 전용지점 0.2

★ **미확인을 0 으로 접지 않는다.**

`languages_supported: []` 와 `dedicated_branch_count: null` 은 "없다"가 아니라
"확인하지 못했다"이다. 이걸 0 으로 계산하면 **확인 못 한 은행이 서비스가 나쁜
은행으로 정렬된다** — 근거 없는 주장을 점수로 만드는 것이고, 이 서비스가 가장
하지 말아야 할 일이다. 그래서 미확인 항목은 **분모에서도 빠진다**(가중 평균을
아는 항목끼리만 낸다). 무엇을 확인하지 못했는지는 `unverified` 로 함께 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.matrix.loader import Institution, VisaRule
from app.schemas.common import EvidenceStatus

# ── 공개되는 상수 (ranking-policy 가 이 값을 읽는다) ──────────────────
W_VISA_FIT = 0.5
W_DOC_BURDEN = 0.3
W_CHANNEL_ACCESS = 0.2

VISA_FIT_BY_STATUS = {
    EvidenceStatus.OFFICIAL: 1.0,
    EvidenceStatus.INFERRED: 0.6,
    EvidenceStatus.UNKNOWN: 0.0,
}

# channel_access 내부 배분
W_LANGUAGE_SUPPORT = 0.5
W_NON_FACE_TO_FACE = 0.3
W_DEDICATED_BRANCH = 0.2


@dataclass(frozen=True, slots=True)
class FitResult:
    """점수와 **그 점수가 무엇으로 만들어졌는지**를 함께 돌려준다.

    화면이 `reasons` 를 그대로 보여줄 수 있어야 "기준 공개"가 성립한다.
    """

    score: float | None  # 아는 항목이 하나도 없으면 None — 0.0 이 아니다
    reasons: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()


def _channel_access(inst: Institution) -> tuple[float | None, list[str], list[str]]:
    """확인된 항목끼리만 가중 평균. 미확인은 분자·분모 양쪽에서 빠진다."""
    known_weight = 0.0
    earned = 0.0
    reasons: list[str] = []
    unverified: list[str] = []

    # ① 외국어 지원 — languages_status 가 unknown 이면 판단하지 않는다
    if inst.languages_status is EvidenceStatus.UNKNOWN:
        unverified.append("language_support")
    else:
        known_weight += W_LANGUAGE_SUPPORT
        if inst.languages_supported:
            earned += W_LANGUAGE_SUPPORT
            reasons.append("language_support")

    # ② 비대면 — 채널 목록 자체가 official 이라 "없음"도 확인된 사실이다
    if inst.status is EvidenceStatus.UNKNOWN:
        unverified.append("non_face_to_face")
    else:
        known_weight += W_NON_FACE_TO_FACE
        if inst.online_available:
            earned += W_NON_FACE_TO_FACE
            reasons.append("non_face_to_face")

    # ③ 전용지점 — null 은 "0곳"이 아니다
    if inst.dedicated_branch_count is None:
        unverified.append("dedicated_branch")
    else:
        known_weight += W_DEDICATED_BRANCH
        if inst.dedicated_branch_count > 0:
            earned += W_DEDICATED_BRANCH
            reasons.append("dedicated_branch")

    if known_weight == 0.0:
        return None, reasons, unverified
    return earned / known_weight, reasons, unverified


def _doc_burden(rule: VisaRule | None, max_docs: int) -> tuple[float | None, list[str]]:
    """서류 부담. 요건 자체가 미확인이면 점수를 만들지 않는다.

    `required_docs: []` 는 "서류가 없다"가 아니라 대개 "확인하지 못했다"이다.
    `account_open` 이 unknown 인 셀의 빈 배열을 **서류 0건(만점)** 으로 읽으면
    아무것도 모르는 은행이 1위가 된다.
    """
    if rule is None or rule.account_open is EvidenceStatus.UNKNOWN:
        return None, []
    if max_docs <= 0:
        return 1.0, ["doc_simplicity"]
    burden = 1.0 - (len(rule.required_docs) / max_docs)
    return burden, (["doc_simplicity"] if burden >= 0.5 else [])


def score_institution(inst: Institution, visa: str | None, max_docs: int) -> FitResult:
    rule = inst.rule_for(visa)
    status = rule.account_open if rule else EvidenceStatus.UNKNOWN

    reasons: list[str] = []
    unverified: list[str] = []

    # visa_fit 은 항상 계산된다 — unknown(0.0) 도 "근거가 없다"는 확인된 정보다.
    terms: list[tuple[float, float]] = [(W_VISA_FIT, VISA_FIT_BY_STATUS[status])]
    if status is EvidenceStatus.OFFICIAL:
        reasons.append("visa_match")
    else:
        unverified.append("visa_requirements")

    burden, burden_reasons = _doc_burden(rule, max_docs)
    if burden is None:
        unverified.append("required_docs")
    else:
        terms.append((W_DOC_BURDEN, burden))
        reasons.extend(burden_reasons)

    access, access_reasons, access_unverified = _channel_access(inst)
    reasons.extend(access_reasons)
    unverified.extend(access_unverified)
    if access is not None:
        terms.append((W_CHANNEL_ACCESS, access))

    total_weight = sum(w for w, _ in terms)
    score = None if total_weight == 0 else round(
        sum(w * v for w, v in terms) / total_weight, 4
    )
    return FitResult(score=score, reasons=tuple(reasons), unverified=tuple(unverified))


def max_required_docs(institutions, visa: str | None) -> int:
    """doc_burden 의 분모. **확인된 셀만** 센다."""
    counts = [
        len(rule.required_docs)
        for inst in institutions
        if (rule := inst.rule_for(visa)) and rule.account_open is not EvidenceStatus.UNKNOWN
    ]
    return max(counts, default=0)


def ranking_policy() -> dict:
    """화면·API 에 공개하는 산식. **상수를 읽어서** 만든다 (사본을 두지 않는다)."""
    return {
        "formula": "fit_score = 0.5×visa_fit + 0.3×doc_burden + 0.2×channel_access",
        "weights": {
            "visa_fit": W_VISA_FIT,
            "doc_burden": W_DOC_BURDEN,
            "channel_access": W_CHANNEL_ACCESS,
        },
        "visa_fit_by_status": {k.value: v for k, v in VISA_FIT_BY_STATUS.items()},
        "channel_access_weights": {
            "language_support": W_LANGUAGE_SUPPORT,
            "non_face_to_face": W_NON_FACE_TO_FACE,
            "dedicated_branch": W_DEDICATED_BRANCH,
        },
        "excluded_inputs": ["제휴", "수수료", "광고", "이용자 클릭"],
        "unverified_handling": (
            "확인하지 못한 항목은 0 점이 아니라 계산에서 제외한다(가중 평균을 아는 "
            "항목끼리만 낸다). 미확인을 낮은 점수로 바꾸면 근거 없는 주장이 된다."
        ),
    }

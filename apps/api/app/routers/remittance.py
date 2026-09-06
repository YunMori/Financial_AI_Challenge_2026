"""`POST /api/v1/remittance/simulate` — F6 해외송금 시뮬레이터 (planner §9.2).

**LLM 이 없다.** 환율 조회 + 순수 함수 계산으로 끝난다.

★ **환율을 못 얻어도 200 이다.** `rate=null` 과 사유를 담아 돌려주고, 한도 안내는
  그대로 나간다 — 환율이 없다고 화면 전체가 죽을 이유가 없다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.schemas.remittance import (
    LimitOut,
    RateOut,
    RemittanceRequest,
    RemittanceResponse,
)
from app.tools.ecos_client import EcosUnavailable, fetch_usd_rate
from app.tools.remittance_calc import (
    LimitCheck,
    check_annual,
    check_per_transaction,
    load_limits,
    to_usd,
)
from app.util.i18n_text import localized

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["remittance"])


def _out(check: LimitCheck, evidence: tuple[dict, ...]) -> LimitOut:
    return LimitOut(
        status=check.status,
        limit_usd=check.limit,
        used_usd=check.used,
        remaining_usd=check.remaining,
        exceeds=check.exceeds,
        evidence=[dict(e) for e in evidence],
    )


@router.post("/remittance/simulate", response_model=RemittanceResponse)
def simulate(body: RemittanceRequest) -> RemittanceResponse:
    limits = load_limits()

    rate_out: RateOut | None = None
    reason: str | None = None
    amount_usd: float | None = None
    try:
        rate, reused = fetch_usd_rate()
        from datetime import date

        rate_out = RateOut(
            value=rate.value,
            quoted_at=rate.quoted_at.isoformat(),
            source=rate.source,
            basis=rate.basis,
            is_stale=reused or rate.is_stale(date.today()),
        )
        amount_usd = to_usd(body.amount_krw, rate.value).amount_usd
    except EcosUnavailable as e:
        log.warning("ECOS 조회 불가: %s", e.reason)
        reason = e.reason

    annual = check_annual(limits.annual_no_doc_usd, body.self_declared_ytd_usd)
    # 건당 한도는 환산액이 있어야 판정할 수 있다. 없으면 판정하지 않는다.
    per_tx = check_per_transaction(
        limits.per_transaction_no_doc_usd, amount_usd if amount_usd is not None else 0.0
    )
    if amount_usd is None:
        per_tx = LimitCheck(status=per_tx.status, limit=per_tx.limit,
                            used=None, remaining=None, exceeds=None)

    return RemittanceResponse(
        lang=body.lang,
        amount_krw=body.amount_krw,
        amount_usd=amount_usd,
        rate=rate_out,
        rate_unavailable_reason=reason,
        annual=_out(annual, limits.annual_no_doc_usd.evidence),
        per_transaction=_out(per_tx, limits.per_transaction_no_doc_usd.evidence),
        notes=localized(limits.notes_block, "notes", body.lang),
    )

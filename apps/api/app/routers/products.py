"""`GET /api/v1/products` — F7 금융상품 비교 (planner §9.2).

**생성 호출이 없다.** 외부 조회 → 조인 → 정렬로 끝난다.

★ **외부가 죽어도 200 을 낸다.** `available=false` 와 사유를 담아서다. 502 를
  내면 화면이 "우리 서비스가 고장났다"로 그리고, 빈 목록을 내면 "그런 상품이
  없다"로 그린다. 둘 다 사실이 아니다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.schemas.common import Lang
from app.schemas.product import (
    ProductCardOut,
    ProductKind,
    ProductsResponse,
    RateOption,
    SortPolicy,
)
from app.tools.fss_client import FssUnavailable, fetch
from app.tools.product_compare import build_cards, sort_policy

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["products"])


@router.get("/products", response_model=ProductsResponse)
def products(
    lang: Lang = Query(default=Lang.KO),
    kind: ProductKind = Query(default="deposit"),
    save_trm: str | None = Query(
        default=None,
        description="저축기간(개월). 예·적금에서 고르지 않으면 기간이 섞여 비교가 성립하지 않는다",
    ),
    limit: int = Query(default=20, ge=1, le=100),
) -> ProductsResponse:
    try:
        raw = fetch(kind)
    except FssUnavailable as e:
        # 사유는 로그와 응답에 남기되, 화면 문구는 프론트의 i18n 이 갖는다.
        log.warning("FSS 조회 불가 (%s): %s", kind, e.reason)
        return ProductsResponse(
            lang=lang, kind=kind, save_trm=save_trm,
            available=False, unavailable_reason=e.reason,
            sort_policy=SortPolicy(**sort_policy(kind)),
        )

    cards = build_cards(raw, save_trm=save_trm)[:limit]
    return ProductsResponse(
        lang=lang,
        kind=kind,
        save_trm=save_trm,
        available=True,
        results=[
            ProductCardOut(
                fin_co_no=c.fin_co_no,
                fin_prdt_cd=c.fin_prdt_cd,
                company=c.company,
                product=c.product,
                join_way=c.join_way,
                join_deny=c.join_deny,
                join_member=c.join_member,
                etc_note=c.etc_note,
                max_limit=c.max_limit,
                options=[
                    RateOption(
                        save_trm=o.save_trm, rate_type=o.rate_type,
                        rate=o.rate, rate_max=o.rate_max,
                    )
                    for o in c.options
                ],
                sort_value=c.sort_value,
                dcls_month=c.dcls_month,
            )
            for c in cards
        ],
        sort_policy=SortPolicy(**sort_policy(kind)),
        dcls_month=raw.dcls_month,
        fetched_on=raw.fetched_on.isoformat(),
    )


@router.get("/products/sort-policy", response_model=SortPolicy)
def policy(kind: ProductKind = Query(default="deposit")) -> SortPolicy:
    """정렬 기준 공개 (기획서 14.3).

    `tools/product_compare.py` 의 상수를 읽어서 만든다 — 문서용 사본을 두면
    기준이 바뀔 때 여기만 옛날 값으로 남는다(F2 와 같은 이유).
    """
    return SortPolicy(**sort_policy(kind))

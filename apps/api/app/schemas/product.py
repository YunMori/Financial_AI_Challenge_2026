"""F7 금융상품 비교 스키마 (planner §9.2).

`docs/functional-spec.md` F7 의 2)·4) 항목 소스다.

★ **`available` 이 있는 이유.** 외부 API 가 죽었거나 키가 없을 때 빈 목록을
  돌려주면 화면은 "그런 상품이 없다"로 그린다. 없는 것과 못 가져온 것은 다르다.

★ **외국인 가입 가능 여부 필드가 없다.** 원문에 그 정보가 없기 때문이다.
  `join_deny`·`join_member` 를 원문 그대로 싣고, 가입 가능 여부는 `caveat` 로
  "확인 필요"를 명시한다 — 없는 것을 있는 것처럼 만드는 필드를 두지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Lang, Tier

ProductKind = Literal["deposit", "saving", "credit_loan"]


class RateOption(BaseModel):
    save_trm: str | None = Field(default=None, description="저축기간(개월). 대출은 없음")
    rate_type: str = Field(default="", description="단리/복리 또는 대출금리 유형")
    rate: float | None = Field(default=None, description="기본금리(예적금) / 평균금리(대출)")
    rate_max: float | None = Field(default=None, description="최고우대금리. 대출은 없음")


class ProductCardOut(BaseModel):
    fin_co_no: str
    fin_prdt_cd: str
    company: str
    product: str
    join_way: str = Field(default="", description="가입 방법 — 원문 그대로")
    join_deny: str = Field(default="", description="가입제한 1/2/3 — **원문 코드 그대로**")
    join_member: str = Field(default="", description="가입대상 — 원문 그대로")
    etc_note: str = ""
    max_limit: float | None = None
    options: list[RateOption] = Field(default_factory=list)
    sort_value: float | None = Field(
        default=None, description="정렬에 쓴 값. 없으면 공시가 없다는 뜻이며 맨 뒤로 간다"
    )
    dcls_month: str = Field(default="", description="공시 기준 월")


class SortPolicy(BaseModel):
    """정렬 기준 공개 (기획서 14.3)."""

    kind: ProductKind
    sort_input: str
    direction: Literal["asc", "desc"]
    excluded_inputs: list[str]
    undisclosed_handling: str
    foreigner_eligibility: str


class ProductsResponse(BaseModel):
    lang: Lang
    kind: ProductKind
    save_trm: str | None = None
    available: bool = Field(
        description="False 면 조회하지 못한 것이다 — **상품이 없다는 뜻이 아니다**"
    )
    unavailable_reason: str | None = Field(
        default=None, description="운영·디버깅용. 화면에는 i18n 문구가 나간다"
    )
    results: list[ProductCardOut] = Field(default_factory=list)
    sort_policy: SortPolicy | None = None
    dcls_month: str = ""
    fetched_on: str | None = None
    disclaimer_tier: Tier = Field(
        default=Tier.B,
        description=(
            "상품 조건 비교라 항상 B 다. 공시값 자체는 공식이지만 "
            "'당신이 가입할 수 있는가'는 기관 심사라 A 로 올릴 수 없다."
        ),
    )

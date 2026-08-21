"""F6 해외송금 시뮬레이터 스키마 (planner §9.2).

★ **환율에 `quoted_at` 이 필수다.** ECOS 는 일별 고시라 "실시간"이 아니다
  (`spec-changes.md` #A). 날짜 없는 환율은 이 서비스에서 낼 수 없는 숫자다.

★ **`used_usd` 는 자가 입력이다.** ORIS 를 조회하지 않는다. 응답에 그 사실을
  담는 필드(`used_is_self_declared`)를 두어, 화면이 빠뜨릴 수 없게 한다.

★ **한도가 unknown 이면 숫자가 없다.** `remaining`·`exceeds` 가 `None` 으로
  나가고 화면은 "확인 필요"로 그린다 — "한도를 넘지 않았다"고 말하는 것도
  근거 없는 주장이다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import EvidenceStatus, Lang, Tier


class RateOut(BaseModel):
    value: float = Field(description="원/미국달러")
    quoted_at: str = Field(description="고시 기준일 (YYYY-MM-DD). **필수**")
    source: str = "한국은행 ECOS"
    basis: str = Field(default="당일 고시 매매기준율", description="실시간 시세가 아니다")
    is_stale: bool = Field(
        default=False,
        description="마지막 성공값을 재사용했거나 고시일이 오래됐다 — 화면에 표시한다",
    )


class LimitOut(BaseModel):
    status: EvidenceStatus
    limit_usd: float | None = None
    used_usd: float | None = None
    remaining_usd: float | None = None
    exceeds: bool | None = Field(
        default=None, description="unknown 이면 None — '넘지 않았다'고도 말하지 않는다"
    )
    evidence: list[dict] = Field(default_factory=list)


class RemittanceRequest(BaseModel):
    lang: Lang = Lang.KO
    amount_krw: float = Field(gt=0, description="보내려는 원화 금액")
    self_declared_ytd_usd: float | None = Field(
        default=None, ge=0,
        description="올해 이미 보낸 금액(달러). **이용자 자가 입력** — ORIS 조회가 아니다",
    )


class RemittanceResponse(BaseModel):
    lang: Lang
    amount_krw: float
    amount_usd: float | None = Field(default=None, description="환율을 못 얻으면 None")
    rate: RateOut | None = None
    rate_unavailable_reason: str | None = None
    annual: LimitOut
    per_transaction: LimitOut
    used_is_self_declared: bool = Field(
        default=True,
        description="항상 True — 이 서비스는 ORIS 를 조회하지 않는다",
    )
    notes: str = Field(default="", description="한도 카탈로그의 안내문 (요청 언어)")
    disclaimer_tier: Tier = Field(
        default=Tier.B,
        description="환산과 한도 안내는 통상적 안내다. 개별 승인은 금융기관 심사(계층 C)",
    )

"""F8 사기 유형 대조 스키마 (planner §9.2, §10-F8).

`docs/functional-spec.md` F8 의 2)·4) 항목 소스다. 필드를 바꾸면 명세서도
함께 바꾼다(문서 서두 규칙).

★ **판정 필드가 없다.** 이 응답에는 "사기인가 아닌가"를 담는 필드가 의도적으로
  없다. 개별 사안 판단은 계층 C 이고, 스키마에 그 자리를 만들어 두면 언젠가
  누군가 채운다. 화면이 하는 일은 **대조**이며 판단은 이용자와 수사기관의 몫이다.

★ **생성이 없다.** F2 와 같이 조회 결과를 그대로 돌려주는 결정적 경로라
  `ai_generated` 가 없다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Lang, Tier
from app.schemas.institution import MatrixEvidence


class ScamType(BaseModel):
    """대조 항목 하나."""

    code: str
    certainty: Literal["definite", "warning"] = Field(
        description=(
            "**원문의 단정 강도**이지 우리 판단이 아니다. definite 는 출처가 "
            "'100% 사기'라고 단정한 항목, warning 은 예방 수칙으로 제시한 항목."
        )
    )
    title: str = Field(description="요청 언어 표기")
    body: str = Field(description="요청 언어 표기")
    evidence: list[MatrixEvidence] = Field(default_factory=list)


class ResponseStep(BaseModel):
    """피해 발생 시 조치 한 단계.

    **순서가 내용이다.** 지급정지가 신고보다 먼저인 것은 돈이 빠져나가는 것을
    막는 일이 환급 절차보다 앞서기 때문이다. 화면이 순서를 바꾸면 안 된다.
    """

    seq: int
    label: str = Field(description="요청 언어 표기")


class ScamContact(BaseModel):
    """공식 연락처. **우리 번호가 아니라 공공기관 번호다.**"""

    code: str
    number: str
    org: str = Field(description="요청 언어 표기")
    role: str = Field(description="요청 언어 표기")
    primary: bool = Field(default=False, description="일원화된 신고 창구인가")
    evidence: list[MatrixEvidence] = Field(default_factory=list)


class ScamResponse(BaseModel):
    lang: Lang
    types: list[ScamType]
    response_steps: list[ResponseStep]
    response_evidence: list[MatrixEvidence] = Field(default_factory=list)
    contacts: list[ScamContact]
    updated_at: str
    disclaimer_tier: Tier = Field(
        default=Tier.C,
        description=(
            "★ 항상 C 다. 개별 사안이 사기인지 여부는 생성하지 않는다는 것을 "
            "응답 자체가 말한다 — 화면은 이 값으로 판정 거부 안내를 띄운다."
        ),
    )

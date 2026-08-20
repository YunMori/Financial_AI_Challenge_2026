"""F3 서류 체크리스트 스키마 (planner §9.2).

`docs/functional-spec.md` F3 의 2)·4) 항목 소스다.

**세션 정보는 문서에 들어가되 서버에 저장되지 않는다.** 응답은 스트리밍이고
생성된 PDF 는 어디에도 남지 않는다 — F1 의 "수집 범위를 못 박는다"가
여기까지 이어진다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.chat import Purpose, VisaCode
from app.schemas.common import EvidenceStatus, Lang, Tier
from app.schemas.institution import MatrixEvidence, RequiredDoc

SectionKey = Literal["base", "account_opening", "foreign_registration"]


class ChecklistRequest(BaseModel):
    """전 항목이 열거값이다 — F1 과 같은 원칙(자유 입력 없음)."""

    lang: Lang
    visa: VisaCode | None = None
    purpose: Purpose | None = None
    inst_code: str | None = Field(
        default=None, description="매트릭스에 없는 코드는 422"
    )
    include_ko: bool = Field(
        default=True,
        description="한국어 병기. 창구 제시용이므로 기본값이 True 다",
    )


class ChecklistSection(BaseModel):
    """체크리스트의 한 절.

    ★ 절을 나누는 것이 이 기능의 핵심이다. 금융위(계좌개설 증빙)와
    법무부(외국인등록 제출서류)는 발급 기관도 제출처도 다르다. 한 표에 놓으면
    이용자가 재학증명서를 들고 은행에 간다.
    """

    key: SectionKey
    title: str = Field(description="요청 언어 제목")
    status: EvidenceStatus
    items: list[RequiredDoc] = Field(default_factory=list)
    notes: str = ""
    caveat: str = Field(default="", description="원문이 예시임을 밝히는 주의문구")
    evidence: list[MatrixEvidence] = Field(default_factory=list)


class ChecklistResponse(BaseModel):
    """HTML 체크리스트용 JSON.

    PDF 와 **같은 조립 결과**를 쓴다. 조판이 검증되지 않은 언어에서 PDF 를
    빼고 HTML 만 제공하는 planner §12.3 경로가 여기서 열린다.
    """

    lang: Lang
    visa: str | None = None
    purpose: str | None = None
    institution: str | None = Field(default=None, description="기관 표기(요청 언어)")
    sections: list[ChecklistSection]
    disclaimer_tier: Tier
    generated_at: str = Field(description="발급 기준일 (ISO)")
    filename: str = Field(description="PDF 로 받을 때의 파일명")

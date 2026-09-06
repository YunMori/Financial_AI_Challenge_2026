"""F2 계좌개설 내비게이터 스키마 (planner §9.2).

`docs/functional-spec.md` F2 의 2)·4) 항목 소스다. 필드를 바꾸면 명세서도
함께 바꾼다(문서 서두 규칙).

**생성이 없다.** 조회 결과를 그대로 돌려주는 결정적 경로이며, LLM 설명 문장은
M1 에서 제외했다(`docs/spec-changes.md`). 그래서 이 응답에는 `tier` 는 있어도
`ai_generated` 는 없다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import EvidenceStatus, Lang, Tier


class MatrixEvidence(BaseModel):
    """근거 한 건. `EvidenceRef`(검색 인용)와 달리 **문서 단위**다."""

    doc_id: str
    publisher: str = Field(default="금융위원회")
    published_at: str | None = None
    url: str
    note: str = ""


class RequiredDoc(BaseModel):
    """서류 한 건. 라벨은 `corpus/glossary/glossary.csv` 가 소스다.

    오역이 치명적인 항목이라 LLM 번역에 맡기지 않는다 — "재직증명서"가
    "work certificate" 가 되면 이용자가 창구에서 다른 서류를 요구받는다.
    """

    code: str
    label: str = Field(description="요청 언어 표기")
    label_ko: str = Field(description="창구에서 보여줄 한국어 원어")


class InstitutionCard(BaseModel):
    """기관 카드 하나.

    ★ `account_open` 과 기관 단위 `status` 는 **다른 것**이다. 모바일
    외국인등록증 수용 여부는 official 로 확인됐지만(=`status`), 그 은행의
    체류자격별 계좌개설 요건은 별개이고 대개 unknown 이다(=`account_open`).
    화면이 이 둘을 하나로 합치면 "공식 확인됨"이 실제보다 넓게 읽힌다.
    """

    inst_code: str
    inst_name: str = Field(description="요청 언어 표기")
    inst_name_ko: str

    status: EvidenceStatus = Field(description="기관 단위(외국인 채널) 확인 상태")
    account_open: EvidenceStatus = Field(description="요청한 체류자격의 계좌개설 요건 상태")

    channels: list[str] = Field(default_factory=list, description="branch / online")
    mobile_arc_accepted: bool = False
    mobile_arc_since: str | None = None

    required_docs: list[RequiredDoc] = Field(default_factory=list)
    purpose_docs: list[RequiredDoc] = Field(default_factory=list)

    fit_score: float | None = Field(
        default=None,
        description="아는 항목이 하나도 없으면 null — 0.0 과 구분한다",
    )
    fit_reason: list[str] = Field(default_factory=list, description="점수를 만든 확인된 항목")
    unverified: list[str] = Field(
        default_factory=list,
        description="★ 확인하지 못한 항목. 점수에서 제외됐고 화면에 그대로 표시한다",
    )

    notes: str = ""
    evidence: list[MatrixEvidence] = Field(default_factory=list)


class InstitutionsResponse(BaseModel):
    visa: str | None = None
    lang: Lang
    results: list[InstitutionCard] = Field(description="fit_score 내림차순")
    unknown_institutions: list[str] = Field(
        default_factory=list,
        description=(
            "★ 요청한 체류자격의 계좌개설 요건을 확인하지 못한 기관 코드. "
            "카드 배열에 섞지 않고 따로 둔다 — 섞으면 화면에서 정직 표시가 흐려진다"
        ),
    )
    disclaimer_tier: Tier = Field(
        description="official 근거가 하나라도 있으면 B, 전부 unknown 이면 C"
    )
    updated_at: str = Field(description="매트릭스 갱신일")


class RankingPolicy(BaseModel):
    """산식 공개 (기획서 14.3). `app/matrix/fit.py` 의 상수를 읽어 만든다."""

    formula: str
    weights: dict[str, float]
    visa_fit_by_status: dict[str, float]
    channel_access_weights: dict[str, float]
    excluded_inputs: list[str]
    unverified_handling: str

"""`GET /api/v1/institutions` — F2 계좌개설 내비게이터 (planner §9.2).

**생성 호출이 없다.** 매트릭스 조회 → 정렬 → 반환으로 끝나는 결정적 경로다.
planner §10-F2 는 "설명 문장만 LLM 생성"을 두었으나 M1 에서 제외했다 —
조회 결과가 대부분 unknown 이라 생성 문장이 정적 문구보다 나을 것이 없는데
카드마다 지연을 얹는다 (`docs/spec-changes.md`).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.matrix.fit import ranking_policy
from app.matrix.service import build_response
from app.schemas.chat import VisaCode
from app.schemas.common import Lang
from app.schemas.institution import InstitutionsResponse, RankingPolicy

router = APIRouter(prefix="/api/v1", tags=["institutions"])


@router.get("/institutions", response_model=InstitutionsResponse)
def institutions(
    lang: Lang = Query(default=Lang.KO),
    visa: VisaCode | None = Query(default=None, description="없으면 기관 단위 정보만"),
) -> InstitutionsResponse:
    return build_response(lang=lang, visa=visa.value if visa else None)


@router.get("/institutions/ranking-policy", response_model=RankingPolicy)
def policy() -> RankingPolicy:
    """정렬 기준 공개 (기획서 14.3).

    `app/matrix/fit.py` 의 상수를 읽어서 만든다. 문서용 사본을 두면 산식이
    바뀔 때 여기만 옛날 값으로 남는다.
    """
    return RankingPolicy(**ranking_policy())

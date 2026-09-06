"""`GET /api/v1/scam` — F8 사기 유형 대조 (planner §9.2).

**생성 호출이 없다.** 카탈로그 조회로 끝나는 결정적 경로다 — F2 와 같은 성질이며,
답이 매번 같아 심사 시연에서 재현성이 보장된다.

★ **판정 엔드포인트가 아니다.** "이 전화가 사기인가요?"는 `POST /chat` 으로 가고
  거기서 계층 C 로 거절된다(`tiering/rules.py`). 여기에 판정 파라미터를 추가하고
  싶어지면 그것이 곧 설계를 무너뜨리는 지점이다 — planner §7.1 참조.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.matrix.scam import build_response
from app.schemas.common import Lang
from app.schemas.scam import ScamResponse

router = APIRouter(prefix="/api/v1", tags=["scam"])


@router.get("/scam", response_model=ScamResponse)
def scam(lang: Lang = Query(default=Lang.KO)) -> ScamResponse:
    return build_response(lang=lang)

"""`POST /api/v1/checklist` — F3 서류 체크리스트 (planner §9.2).

두 경로가 **같은 조립 결과**를 쓴다.

    POST /checklist          → application/pdf  (창구 제시용)
    POST /checklist/preview  → JSON             (화면 내 HTML 체크리스트)

preview 가 있는 이유는 둘이다. ① 조판이 검증되지 않은 언어에서 PDF 를 빼고
HTML 만 제공하는 planner §12.3 대응 경로. ② **WeasyPrint 가 없는 환경에서도
기능이 절반은 산다** — 로컬 개발기(Windows·macOS)가 그런 환경이다.

**서버에 저장하지 않는다.** 생성한 PDF 는 스트리밍으로 나가고 어디에도 남지
않는다. F1 이 "수집 범위를 못 박는다"고 한 원칙이 산출물까지 이어진다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.docs.checklist import build_checklist
from app.matrix.loader import get_matrix
from app.schemas.checklist import ChecklistRequest, ChecklistResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["checklist"])


def _assemble(body: ChecklistRequest) -> ChecklistResponse:
    institution = None
    if body.inst_code:
        inst = get_matrix().get(body.inst_code)
        if inst is None:
            # 매트릭스에 없는 코드는 조용히 무시하지 않는다 — 화면이 엉뚱한
            # 기관을 보여주고 있다는 뜻이므로 드러내야 한다.
            raise HTTPException(status_code=422, detail=f"알 수 없는 기관: {body.inst_code}")
        institution = inst.name(body.lang.value)

    return build_checklist(
        lang=body.lang,
        visa=body.visa.value if body.visa else None,
        purpose=body.purpose.value if body.purpose else None,
        institution=institution,
    )


@router.post("/checklist/preview", response_model=ChecklistResponse)
def preview(body: ChecklistRequest) -> ChecklistResponse:
    """화면 내 HTML 체크리스트용. PDF 와 같은 내용이다."""
    return _assemble(body)


@router.post("/checklist")
def checklist(body: ChecklistRequest) -> Response:
    doc = _assemble(body)

    try:
        from app.docs.pdf_render import render_pdf

        pdf = render_pdf(doc, include_ko=body.include_ko)
    except (ImportError, OSError) as exc:
        # ★ `OSError` 를 함께 잡는다. WeasyPrint 는 모듈은 찾지만 그 안에서
        #   libgobject 를 dlopen 하다 **OSError** 로 죽는다 — `ImportError` 만
        #   잡으면 500 이 나간다(실측: Windows 개발기, error 0x7e).
        #   로컬에서는 이것이 정상이고, **컨테이너에서 이 로그가 보이면
        #   Dockerfile 의 조판 의존성이 깨진 것**이다.
        log.error("PDF 렌더 불가 (WeasyPrint 시스템 의존성 부재): %s", exc)
        raise HTTPException(
            status_code=503,
            detail="PDF 생성을 사용할 수 없습니다. 화면 내 체크리스트를 이용하세요.",
        ) from exc

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{doc.filename}"',
            # 개인 프로필이 담긴 문서다. 중간 캐시에 남지 않게 한다.
            "Cache-Control": "no-store",
        },
    )

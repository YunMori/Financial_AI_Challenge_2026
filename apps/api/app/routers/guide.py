"""`POST /api/v1/guide/limit-release` — F4 한도제한계좌 해제 가이드.

**새 생성 경로를 만들지 않는다.** `ChatPipeline` 이 ①~⑩ 을 이미 관통하고 있고,
F4 가 필요로 하는 것은 그 앞뒤 두 가지뿐이다.

    앞  프로필(체류자격 × 거래목적) → 한국어 질의 조립
    뒤  답변 뒤에 F3 체크리스트로 잇는 `next_action`

★ **질의를 한국어로 조립하는 것이 핵심이다.** 챗봇 경로는 이용자가 쓴 언어를
  ③ 정규화가 한국어 검색어로 옮기는데, 그 품질이 en 86.4% / vi 90.5% 로
  목표(92)에 못 미친다(ADR-004 §③). F4 는 질의를 **우리가 만들기 때문에**
  정규화를 통과할 필요가 없다 — 처음부터 정확한 한국어 검색어로 들어간다.
  답변 언어는 `lang` 이 그대로 정하므로 다국어성은 잃지 않는다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.routers.chat import event_stream
from app.schemas.chat import ChatRequest, NextAction, Purpose, SessionContext
from app.schemas.common import Lang

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["guide"])


class LimitReleaseRequest(BaseModel):
    """입력은 F1 이 이미 모은 것뿐이다. 새로 묻는 것이 없다."""

    lang: Lang
    context: SessionContext = Field(default_factory=SessionContext)


# 거래목적 → 질의에 넣을 한국어. 사전(glossary)에는 서류·용어만 있고
# 거래목적은 F1 의 열거값이라 여기서 한 번만 매핑한다.
_PURPOSE_KO: dict[Purpose, str] = {
    Purpose.SALARY: "급여 수령",
    Purpose.TUITION: "학비 납부",
    Purpose.LIVING: "생활비",
    Purpose.REMITTANCE: "해외 송금",
    Purpose.BUSINESS: "사업 자금",
}


def build_query(context: SessionContext) -> str:
    """프로필 → 한국어 질의.

    "한도제한계좌"는 복합명사로 사전에 등록돼 있어(`rag/tokenize.py`) BM25 가
    한 토큰으로 다룬다. 체류자격 코드도 형태소 분석 전에 보호된다 — 즉 이
    문장은 **검색이 가장 잘 먹는 형태**로 만들어져 있다.
    """
    parts = ["한도제한계좌 해제"]
    if context.visa:
        parts.append(f"{context.visa.value} 체류자격")
    if context.purposes:
        # 첫 번째 목적만 쓴다. 여러 목적을 이으면 검색어가 넓어져 top1 이 흐려진다.
        parts.append(_PURPOSE_KO[context.purposes[0]])
    parts.append("금융거래목적 증빙 서류")
    return " ".join(parts)


def build_next_action(context: SessionContext) -> NextAction:
    """답변 뒤 F3 연결. 프로필을 그대로 체크리스트 요청 파라미터로 넘긴다."""
    params: dict[str, str] = {}
    if context.visa:
        params["visa"] = context.visa.value
    if context.purposes:
        params["purpose"] = context.purposes[0].value
    return NextAction(type="checklist", label_key="guide.makeChecklist", params=params)


@router.post("/guide/limit-release")
async def limit_release(body: LimitReleaseRequest, request: Request) -> StreamingResponse:
    query = build_query(body.context)
    log.info("guide/limit-release lang=%s visa=%s query=%r",
             body.lang.value, body.context.visa, query)

    req = ChatRequest(lang=body.lang, message=query, context=body.context)
    return StreamingResponse(
        event_stream(req, request, next_action=build_next_action(body.context)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # 프록시 버퍼링 방지 — 없으면 스트리밍이 죽는다
            "Connection": "keep-alive",
        },
    )

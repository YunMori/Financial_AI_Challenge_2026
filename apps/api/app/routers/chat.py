"""`POST /api/v1/chat` — SSE 스트리밍 (planner §9.2).

이벤트 순서: `meta` → `token`* → `citations` → (`invalidate`) → `done`

**`meta` 의 tier 는 잠정값이다.** 최종 tier 는 `done` 에서 확정된다.
출력 검사에 실패하면 `invalidate` 를 보내고 클라이언트가 표시된 텍스트를
폴백 카드로 **교체**한다. 이 처리를 빠뜨리면 "차단했다"고 주장하면서
화면엔 환각이 남는 사고가 난다.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.pipeline import PipelineEvent, build_pipeline
from app.schemas.chat import (
    ChatRequest,
    CitationsEvent,
    DoneEvent,
    InvalidateEvent,
    MetaEvent,
    NextAction,
    TokenEvent,
)
from app.schemas.common import Tier

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["chat"])

_pipeline = None


def get_pipeline():
    """무거운 초기화를 첫 요청까지 미룬다 — `/healthz` 가 가벼워야 하므로."""
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()
    return _pipeline


def sse(event: str, payload) -> str:
    body = payload if isinstance(payload, str) else payload.model_dump_json()
    return f"event: {event}\ndata: {body}\n\n"


async def event_stream(
    req: ChatRequest,
    request: Request,
    next_action: NextAction | None = None,
) -> AsyncIterator[str]:
    """`/chat` 과 F4 가 **같은 스트림**을 쓴다.

    F4 가 생성 경로를 따로 갖지 않는 이유는 이것 하나로 충분하다 — 이벤트 순서,
    `invalidate` 교체, 폴백 처리를 두 벌 유지하면 한쪽이 반드시 뒤처진다.
    다른 것은 `done` 에 붙는 `next_action` 뿐이다.
    """
    pipeline = get_pipeline()
    streamed_any = False

    async for ev in pipeline.run(req):
        if await request.is_disconnected():
            log.info("클라이언트 연결 종료 — 스트림 중단")
            return

        if ev.kind == "meta":
            # 잠정 tier. 근거를 찾았다는 사실만 알린다.
            yield sse("meta", MetaEvent(tier=Tier.A, retrieval=ev.meta))

        elif ev.kind == "token":
            streamed_any = True
            yield sse("token", TokenEvent(t=ev.text))

        elif ev.kind == "final":
            resp = ev.response
            assert resp is not None

            if resp.is_fallback:
                # ★ 이미 텍스트를 흘렸다면 화면을 교체하라고 알린다.
                # 이 처리가 없으면 차단했다고 하면서 환각이 화면에 남는다.
                if streamed_any:
                    yield sse("invalidate", InvalidateEvent(
                        reason=resp.fallback_reason,
                        fallback_text=resp.answer,
                        contacts=resp.contacts or [],
                    ))
                else:
                    # 흘린 게 없으면 폴백 문구를 본문으로 그냥 보낸다.
                    yield sse("token", TokenEvent(t=resp.answer))
                    if resp.contacts:
                        yield sse("invalidate", InvalidateEvent(
                            reason=resp.fallback_reason,
                            fallback_text=resp.answer,
                            contacts=resp.contacts,
                        ))

            if resp.refs:
                yield sse("citations", CitationsEvent(items=resp.refs))

            yield sse("done", DoneEvent(
                tier=resp.tier,
                latency_ms=ev.latency_ms,
                fallback_reason=resp.fallback_reason,
                # ★ 폴백에는 붙이지 않는다. 답하지 못한 뒤에 "이 서류로
                #   체크리스트를 만드세요"를 내밀면 답한 척이 된다.
                next_action=None if resp.is_fallback else next_action,
            ))


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    return StreamingResponse(
        event_stream(body, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # 프록시 버퍼링 방지 — 없으면 스트리밍이 죽는다
            "Connection": "keep-alive",
        },
    )

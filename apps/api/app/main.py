"""FastAPI 진입점.

`/healthz` 는 콜드스타트 대비 웜업 호출 대상이다(배포는 AWS, ADR-004)
(planner §15.3). 무거운 초기화를 여기에 걸면 웜업이 의미를 잃으므로
인덱스 로딩 상태는 조회만 하고 트리거하지 않는다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.routers import chat

settings = get_settings()
logging.basicConfig(level=settings.log_level)

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="K-Buddy API",
    description="체류자격 기반 외국인 금융정착 안내 — 근거 기반 응답",
    version="0.1.0",
    docs_url="/docs",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # 쿠키·인증을 쓰지 않는다. 서버는 상태를 갖지 않음
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-KB-Lang"],
)


app.include_router(chat.router)


class HealthResponse(BaseModel):
    status: str
    version: str
    llm_configured: bool
    index_present: bool


@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
def healthz() -> HealthResponse:
    """가벼운 생존 확인.

    인덱스를 **로드하지 않고 존재 여부만** 본다. 여기서 임베딩 모델을 올리면
    콜드스타트가 그대로 웜업 요청에 실려 §15.3 대응이 무력해진다.
    """
    return HealthResponse(
        status="ok",
        version=app.version,
        llm_configured=settings.llm_enabled,
        index_present=settings.chroma_path.exists(),
    )

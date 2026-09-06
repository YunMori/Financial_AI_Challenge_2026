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
from app.routers import (
    chat,
    checklist,
    guide,
    institutions,
    products,
    remittance,
    scam,
)

settings = get_settings()
logging.basicConfig(level=settings.log_level)

# ★★ **httpx 가 요청 URL 을 INFO 로 찍는다 — 거기에 인증키가 있다.**
#
#   FSS 는 `?auth=<키>`, ECOS 는 경로 세그먼트에 키를 싣는다. `redact.py` 는
#   **예외 메시지**를 지우지만, httpx 는 **성공한 요청도** 로그에 남긴다:
#
#     INFO:httpx:HTTP Request: GET https://finlife.fss.or.kr/...?auth=<키> "200 OK"
#
#   실측 2026-08-21 — 조회가 성공할수록 키가 더 많이 쌓인다. 예외 처리로는
#   절대 못 막는 경로라 로거 자체를 올린다. WARNING 이면 우리가 남기는
#   "FSS 조회 불가" 같은 진단은 그대로 보인다.
#
#   ⚠ 디버깅으로 INFO 를 되돌리면 키가 다시 새어 나온다. 필요하면 그때만
#     한시적으로 켜고 로그 파일을 지운다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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
app.include_router(institutions.router)
app.include_router(checklist.router)
app.include_router(guide.router)
app.include_router(scam.router)
app.include_router(products.router)
app.include_router(remittance.router)


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
        llm_configured=settings.generation_enabled,
        index_present=settings.chroma_path.exists(),
    )

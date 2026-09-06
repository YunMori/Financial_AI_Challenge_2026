"""외부 API 오류 메시지에서 인증키를 지운다.

★★ **이것이 없으면 키가 로그와 API 응답 양쪽으로 새어 나간다.**
   2026-08-21 실측: FSS 가 307 리다이렉트를 내자 `httpx.HTTPStatusError` 의
   메시지에 요청 URL 이 통째로 들어갔고, 그 URL 의 `auth=` 에 인증키가 있었다.
   그대로 `logger.warning()` 과 `unavailable_reason` 으로 나갔다 —
   **로그 파일과 브라우저 응답 두 곳에 평문 키가 남는다.**

   planner §8.2 는 `logger.info(f"query={raw}")` 같은 코드를 금지하고 로깅을
   `log_safe()` 로만 하게 했다. 그 원칙은 이용자 입력에만 적용되는 것이 아니다.

★ **화이트리스트가 아니라 블랙리스트라는 점을 알고 쓴다.** 아래 패턴에 없는
  방식으로 키가 실리면 못 잡는다. 그래서 근본 방어는 "키를 URL 에 넣지 않는 것"
  이지만, FSS·ECOS 는 둘 다 URL 인증만 지원한다 — 그 제약 위에서의 최선이다.
"""

from __future__ import annotations

import re

_MASK = "***"

_PATTERNS = (
    # FSS: `?auth=<key>` 또는 `&auth=<key>` (쿼리스트링)
    re.compile(r"(auth=)[^&\s'\"]+"),
    # ECOS: `/api/StatisticSearch/<key>/json/...` (경로 세그먼트)
    re.compile(r"(/api/[A-Za-z]+/)[^/\s]+(/json)"),
    # 혹시 다른 이름으로 실릴 경우
    re.compile(r"((?:api[_-]?key|apikey|serviceKey|token)=)[^&\s'\"]+", re.IGNORECASE),
)


def redact(text: str) -> str:
    """문자열에서 알려진 인증키 위치를 마스킹한다."""
    out = text
    for pat in _PATTERNS:
        # 그룹이 2개인 패턴(ECOS 경로)은 앞뒤를 모두 살린다.
        out = pat.sub(
            lambda m: m.group(1) + _MASK + (m.group(2) if m.lastindex and m.lastindex >= 2 else ""),
            out,
        )
    return out


def safe_reason(exc: BaseException) -> str:
    """예외를 사유 문자열로 만든다. **키는 지운다.**

    호출부가 `f"{type(e).__name__}: {e}"` 를 직접 만들면 이 함수를 우회하게
    되므로, 외부 클라이언트는 반드시 이쪽을 쓴다.
    """
    return redact(f"{type(exc).__name__}: {exc}")

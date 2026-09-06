"""금융감독원 「금융상품 한눈에」 OpenAPI 클라이언트 (F7, planner §9.2).

★ **이 API 는 "외국인이 가입할 수 있는가"를 말해 주지 않는다.**
  `join_deny`(가입제한 1/2/3)와 `join_member`(가입대상 자유텍스트)가 있지만
  체류자격을 다루지 않는다. 즉 우리가 아는 것은 **상품 조건**뿐이고 외국인
  가입 가능 여부는 `unknown` 이다 — F2 의 3값 규칙과 같은 자리다.
  이 사실을 화면과 응답 양쪽에 싣는다. 조건만 보고 갔다가 창구에서 거절당하면
  그 안내는 없느니만 못하다.

★ **키가 없으면 빈 목록이 아니라 "설정되지 않음"이다.** 빈 목록은 "그런 상품이
  없다"로 읽힌다. `available=False` 와 사유를 함께 돌려준다.

★ **캐시는 하루 1회다** (planner §2.1). 외부 장애가 화면 장애가 되지 않게 하는
  것이 주목적이고, 일일 허용횟수(에러코드 020) 방어가 부수 효과다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal

import httpx

from app.config import get_settings
from app.tools.redact import safe_reason

log = logging.getLogger(__name__)

# ★ **https 다.** 문서 예제는 http 를 쓰지만 서버가 307 로 https 에 리다이렉트하고,
#   `httpx` 는 기본적으로 리다이렉트를 따라가지 않아 조회가 통째로 실패했다
#   (2026-08-21 실측 — urllib 은 따라가므로 수동 확인에서는 성공해 원인이 가려졌다).
#   무엇보다 **인증키가 쿼리스트링에 실린다.** 평문 http 로 보낼 값이 아니다.
BASE_URL = "https://finlife.fss.or.kr/finlifeapi"

# 은행. planner §5.1 의 도메인 화이트리스트와 같은 취지로 권역을 은행에 고정한다 —
# 저축은행·여신전문까지 열면 비교 대상이 성격상 섞인다.
TOP_FIN_GRP_NO = "020000"

ProductKind = Literal["deposit", "saving", "credit_loan"]

_ENDPOINT: dict[ProductKind, str] = {
    "deposit": "depositProductsSearch.json",
    "saving": "savingProductsSearch.json",
    "credit_loan": "creditLoanProductsSearch.json",
}

# 원문 에러코드 → 사람이 읽는 사유. 화면에는 i18n 문구가 나가고 이 값은 로그·디버깅용이다.
_ERR = {
    "010": "인증키 미입력",
    "011": "인증키가 유효하지 않음",
    "020": "일일 허용횟수 초과",
    "100": "금융회사 코드 미입력",
    "900": "데이터 없음",
}


class FssUnavailable(RuntimeError):
    """외부가 응답하지 않거나 키가 없다. **빈 결과와 구분한다.**"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RawProducts:
    """원문 그대로. 가공은 `app.tools.product_compare` 가 한다."""

    kind: ProductKind
    base_list: tuple[dict, ...]
    option_list: tuple[dict, ...]
    fetched_on: date
    dcls_month: str = ""


@dataclass
class _CacheEntry:
    on: date
    value: RawProducts


_cache: dict[ProductKind, _CacheEntry] = {}


def clear_cache() -> None:
    """테스트와 수동 갱신용. 운영 경로에서는 부르지 않는다."""
    _cache.clear()


def _get(url: str, params: dict, timeout: float) -> dict:
    # `follow_redirects` 는 안전망이다. BASE_URL 을 https 로 두어 307 을 애초에
    # 만나지 않게 했지만, 엔드포인트가 또 옮겨가도 조용히 죽지 않게 한다.
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json()


def fetch(
    kind: ProductKind,
    *,
    today: date | None = None,
    fetcher: Callable[[str, dict, float], dict] = _get,
) -> RawProducts:
    """상품 원문을 가져온다. 같은 날 두 번째 호출부터는 캐시다.

    `fetcher` 를 인자로 둔 것은 **키 없이도 조인·정렬을 검증하기 위해서**다.
    실키 없이 픽스처로 테스트를 돌릴 수 있어야 이 코드가 죽은 채로 남지 않는다.
    """
    today = today or date.today()
    hit = _cache.get(kind)
    if hit and hit.on == today:
        return hit.value

    s = get_settings()
    if not s.fss_api_key:
        raise FssUnavailable("FSS_API_KEY 미설정")

    try:
        payload = fetcher(
            f"{BASE_URL}/{_ENDPOINT[kind]}",
            {"auth": s.fss_api_key, "topFinGrpNo": TOP_FIN_GRP_NO, "pageNo": 1},
            s.external_api_timeout_s,
        )
    except Exception as e:  # noqa: BLE001 - 외부 장애 원인을 하나로 좁힌다
        # ★ 예외 메시지에 요청 URL 이 통째로 들어간다 — 그 URL 에 인증키가 있다.
        #   사유는 로그와 API 응답 양쪽으로 나가므로 반드시 지운다(`redact.py`).
        raise FssUnavailable(safe_reason(e)) from e

    result = (payload or {}).get("result") or {}
    err = str(result.get("err_cd", ""))
    if err and err != "000":
        raise FssUnavailable(f"err_cd={err} ({_ERR.get(err, result.get('err_msg', ''))})")

    raw = RawProducts(
        kind=kind,
        base_list=tuple(result.get("baseList") or ()),
        option_list=tuple(result.get("optionList") or ()),
        fetched_on=today,
        dcls_month=str(result.get("baseList", [{}])[0].get("dcls_month", "") or "")
        if result.get("baseList")
        else "",
    )
    _cache[kind] = _CacheEntry(on=today, value=raw)
    log.info("FSS %s 조회: base=%d option=%d dcls_month=%s",
             kind, len(raw.base_list), len(raw.option_list), raw.dcls_month)
    return raw

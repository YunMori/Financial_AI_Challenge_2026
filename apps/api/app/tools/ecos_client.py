"""한국은행 ECOS 환율 조회 (F6, planner §9.2).

★ **실시간 시세가 아니다.** ECOS 는 일별 고시 통계 API 다. 기획서의 "실시간 환율"
  문구는 `docs/spec-changes.md` #A 로 "당일 고시 매매기준율 기준"으로 고쳤고,
  이 모듈이 돌려주는 `quoted_at` 이 그 기준일이다. 화면도 날짜를 함께 적는다 —
  심사에서 가장 짚기 쉬운 지점이다.

★ **장애 시 마지막 성공값을 쓰되 그 사실을 숨기지 않는다** (planner §10-F6 6번).
  `is_stale=True` 와 `quoted_at` 을 함께 돌려주고, 화면이 "OO일 기준"으로 적는다.
  조용히 옛 환율로 계산해 주는 것이 가장 나쁘다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

import httpx

from app.config import get_settings
from app.tools.redact import safe_reason

log = logging.getLogger(__name__)

BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"

# 731Y001 = 일별 주요국 통화의 대원화 환율. 0000001 = 원/미국달러(매매기준율).
STAT_CODE = "731Y001"
CYCLE = "D"
ITEM_USD = "0000001"

# 조회 창. 주말·공휴일에는 고시가 없으므로 하루만 물으면 빈 응답이 온다.
LOOKBACK_DAYS = 10

# 캐시 수명. planner §2.1 "ECOS 1시간 캐시".
CACHE_TTL = timedelta(hours=1)


class EcosUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Rate:
    """원/미국달러 매매기준율 한 건."""

    value: float
    quoted_at: date
    source: str = "한국은행 ECOS"
    basis: str = "당일 고시 매매기준율"

    def is_stale(self, today: date, max_age_days: int = 3) -> bool:
        """고시일이 오래됐는가. 주말·연휴를 감안해 기본 3일이다."""
        return (today - self.quoted_at).days > max_age_days


@dataclass
class _Entry:
    at: datetime
    value: Rate


_cache: dict[str, _Entry] = {}
# 마지막 성공값. 외부가 죽었을 때 **표시와 함께** 재사용한다.
_last_good: Rate | None = None


def clear_cache() -> None:
    global _last_good
    _cache.clear()
    _last_good = None


def _get(url: str, timeout: float) -> dict:
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.json()


def fetch_usd_rate(
    *,
    today: date | None = None,
    now: datetime | None = None,
    fetcher: Callable[[str, float], dict] = _get,
) -> tuple[Rate, bool]:
    """(환율, 마지막성공값을 재사용했는가) 를 돌려준다.

    두 번째 값이 True 면 **지금 값이 아니다** — 호출부가 반드시 화면에 표시한다.
    """
    global _last_good
    today = today or date.today()
    now = now or datetime.now()

    hit = _cache.get(ITEM_USD)
    if hit and now - hit.at < CACHE_TTL:
        return hit.value, False

    s = get_settings()
    if not s.ecos_api_key:
        if _last_good:
            return _last_good, True
        raise EcosUnavailable("ECOS_API_KEY 미설정")

    start = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    url = (
        f"{BASE_URL}/{s.ecos_api_key}/json/kr/1/100/"
        f"{STAT_CODE}/{CYCLE}/{start}/{end}/{ITEM_USD}"
    )
    try:
        payload = fetcher(url, s.external_api_timeout_s)
    except Exception as e:  # noqa: BLE001 - 외부 장애를 하나로 좁힌다
        # ★ ECOS 는 키를 **경로**에 싣는다. 예외 메시지에 URL 이 들어가면
        #   키가 그대로 로그에 남는다 — 반드시 지운다(`redact.py`).
        reason = safe_reason(e)
        if _last_good:
            log.warning("ECOS 조회 실패, 마지막 성공값 사용: %s", reason)
            return _last_good, True
        raise EcosUnavailable(reason) from e

    # 정상 응답은 StatisticSearch, 오류는 RESULT 로 온다.
    if "RESULT" in (payload or {}):
        r = payload["RESULT"]
        reason = f"{r.get('CODE')}: {r.get('MESSAGE')}"
        if _last_good:
            log.warning("ECOS 오류, 마지막 성공값 사용: %s", reason)
            return _last_good, True
        raise EcosUnavailable(reason)

    rows = ((payload or {}).get("StatisticSearch") or {}).get("row") or []
    parsed: list[Rate] = []
    for row in rows:
        raw_value = str(row.get("DATA_VALUE", "")).strip().replace(",", "")
        raw_time = str(row.get("TIME", "")).strip()
        if not raw_value or len(raw_time) != 8:
            continue
        try:
            parsed.append(
                Rate(value=float(raw_value),
                     quoted_at=datetime.strptime(raw_time, "%Y%m%d").date())
            )
        except ValueError:
            continue

    if not parsed:
        if _last_good:
            return _last_good, True
        raise EcosUnavailable("고시값 없음")

    latest = max(parsed, key=lambda r: r.quoted_at)
    _cache[ITEM_USD] = _Entry(at=now, value=latest)
    _last_good = latest
    log.info("ECOS 환율: %s = %.2f (기준일 %s)", ITEM_USD, latest.value, latest.quoted_at)
    return latest, False

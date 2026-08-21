"""F6 해외송금 시뮬레이터 (planner §9.2, §10-F6).

**실키 없이 돈다.** `fetch_usd_rate(fetcher=...)` 에 픽스처를 주입한다.

이 파일이 지키는 주장은 넷이다.

1. **근거 없는 한도를 만들어 내지 않는다.** 지금 `remittance.yaml` 의 한도는
   전부 unknown 이며(fact-check B4·B5·B6 미확인), 그 상태에서 응답에 숫자가
   새어 나오면 안 된다. **"한도를 넘지 않았다"고 말하는 것도 근거 없는 주장이다.**
2. **환율에는 항상 기준일이 붙는다.** ECOS 는 일별 고시라 실시간이 아니다.
3. **마지막 성공값을 쓸 때 숨기지 않는다.** `is_stale=True` 로 드러난다.
4. **계산은 순수 함수다.** 같은 입력에 같은 값이 나온다.
"""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.schemas.common import EvidenceStatus
from app.tools import ecos_client
from app.tools.ecos_client import EcosUnavailable, clear_cache, fetch_usd_rate
from app.tools.remittance_calc import (
    check_annual,
    check_per_transaction,
    load_limits,
    to_usd,
)

TODAY = date(2026, 8, 21)
NOW = datetime(2026, 8, 21, 10, 0, 0)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """★ 테스트를 주변 `.env` 에서 격리한다.

    개발 머신에 실 ECOS 키가 들어오자 "키 없음" 경로 테스트가 깨졌다(2026-08-21).
    환경에 따라 결과가 달라지면 회귀 방어망이 아니게 된다.
    """
    monkeypatch.setenv("ECOS_API_KEY", "")
    clear_cache()
    get_settings.cache_clear()
    yield
    clear_cache()
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def _payload(rows):
    return {"StatisticSearch": {"row": rows}}


ROWS = [
    {"TIME": "20260819", "DATA_VALUE": "1,380.10"},
    {"TIME": "20260820", "DATA_VALUE": "1,392.50"},
]


def _with_key(fn, key="k"):
    import os

    os.environ["ECOS_API_KEY"] = key
    get_settings.cache_clear()
    try:
        return fn()
    finally:
        os.environ.pop("ECOS_API_KEY", None)
        get_settings.cache_clear()


class TestUnknownLimitsLeakNothing:
    def test_catalog_limits_are_currently_unknown(self):
        """이 테스트가 깨지면 한도가 확인됐다는 뜻이다 — fact-check 를 함께 닫아라."""
        lim = load_limits()
        assert lim.annual_no_doc_usd.status is EvidenceStatus.UNKNOWN
        assert lim.annual_no_doc_usd.value is None
        assert lim.per_transaction_no_doc_usd.value is None

    def test_unknown_limit_gives_no_remaining(self):
        lim = load_limits()
        check = check_annual(lim.annual_no_doc_usd, self_declared_used_usd=12000)
        assert check.limit is None
        assert check.remaining is None

    def test_unknown_limit_does_not_claim_within_limit(self):
        """★ 가장 중요한 주장. `exceeds=False` 는 "한도 안이다"라는 **단정**이다."""
        lim = load_limits()
        assert check_annual(lim.annual_no_doc_usd, 1.0).exceeds is None
        assert check_per_transaction(lim.per_transaction_no_doc_usd, 1.0).exceeds is None

    def test_endpoint_emits_no_limit_numbers(self, client):
        r = client.post("/api/v1/remittance/simulate",
                        params=None, json={"lang": "ko", "amount_krw": 3_000_000})
        assert r.status_code == 200
        body = r.json()
        for key in ("annual", "per_transaction"):
            assert body[key]["status"] == "unknown"
            assert body[key]["limit_usd"] is None
            assert body[key]["remaining_usd"] is None
            assert body[key]["exceeds"] is None

    def test_notes_explain_why_no_number(self, client):
        body = client.post("/api/v1/remittance/simulate",
                           json={"lang": "ko", "amount_krw": 1_000_000}).json()
        assert "확인" in body["notes"]


class TestRate:
    def test_latest_quote_is_used_with_its_date(self):
        rate, reused = _with_key(
            lambda: fetch_usd_rate(today=TODAY, now=NOW,
                                   fetcher=lambda url, timeout: _payload(ROWS))
        )
        assert reused is False
        assert rate.value == 1392.50
        assert rate.quoted_at == date(2026, 8, 20)

    def test_basis_is_not_realtime(self):
        rate, _ = _with_key(
            lambda: fetch_usd_rate(today=TODAY, now=NOW,
                                   fetcher=lambda url, timeout: _payload(ROWS))
        )
        assert "매매기준율" in rate.basis
        assert "실시간" not in rate.basis

    def test_no_key_and_no_history_raises(self):
        with pytest.raises(EcosUnavailable):
            fetch_usd_rate(today=TODAY, now=NOW, fetcher=lambda url, timeout: {})

    def test_error_payload_falls_back_to_last_good_and_flags_it(self):
        """★ 옛 환율을 조용히 쓰지 않는다 — reused=True 로 드러난다."""
        _with_key(lambda: fetch_usd_rate(today=TODAY, now=NOW,
                                         fetcher=lambda url, timeout: _payload(ROWS)))
        # 캐시만 비우고 `_last_good` 은 남긴다 — 그래야 폴백 경로를 잴 수 있다.
        ecos_client._cache.clear()

        def boom(url, timeout):
            return {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}

        rate, reused = _with_key(lambda: fetch_usd_rate(today=TODAY, now=NOW, fetcher=boom))
        assert reused is True
        assert rate.value == 1392.50

    def test_cache_within_ttl_does_not_refetch(self):
        calls = {"n": 0}

        def fetcher(url, timeout):
            calls["n"] += 1
            return _payload(ROWS)

        _with_key(lambda: fetch_usd_rate(today=TODAY, now=NOW, fetcher=fetcher))
        _with_key(lambda: fetch_usd_rate(today=TODAY, now=NOW, fetcher=fetcher))
        assert calls["n"] == 1

    def test_endpoint_survives_rate_outage(self, client):
        """환율이 없어도 한도 안내는 나간다 — 화면 전체가 죽지 않는다."""
        body = client.post("/api/v1/remittance/simulate",
                           json={"lang": "ko", "amount_krw": 1_000_000}).json()
        assert body["rate"] is None
        assert body["amount_usd"] is None
        assert body["rate_unavailable_reason"]
        assert body["annual"]["status"] == "unknown"


class TestPureCalculation:
    def test_to_usd_is_deterministic(self):
        a = to_usd(1_392_500, 1392.5)
        b = to_usd(1_392_500, 1392.5)
        assert a == b
        assert a.amount_usd == pytest.approx(1000.0)

    def test_rate_must_be_positive(self):
        with pytest.raises(ValueError):
            to_usd(1000, 0)

    def test_known_limit_math(self):
        """한도가 확인되면 이렇게 계산된다 — 값이 채워질 때를 대비한 계약."""
        from app.tools.remittance_calc import LimitRule

        rule = LimitRule(status=EvidenceStatus.OFFICIAL, value=50_000.0,
                         evidence=({"source_url": "https://example.go.kr"},))
        check = check_annual(rule, self_declared_used_usd=12_000)
        assert check.remaining == 38_000
        assert check.exceeds is False
        assert check_annual(rule, 60_000).exceeds is True


class TestSelfDeclared:
    def test_response_marks_used_as_self_declared(self, client):
        """ORIS 를 조회하지 않는다는 사실이 응답에 남는다 (planner §10-F6 4번)."""
        body = client.post("/api/v1/remittance/simulate",
                           json={"lang": "ko", "amount_krw": 1_000_000,
                                 "self_declared_ytd_usd": 12_000}).json()
        assert body["used_is_self_declared"] is True

    def test_negative_amount_rejected(self, client):
        r = client.post("/api/v1/remittance/simulate",
                        json={"lang": "ko", "amount_krw": -1})
        assert r.status_code == 422

"""F7 금융상품 비교 (planner §9.2, 기획서 14.3 중립성).

**실키 없이 돈다.** `fss_client.fetch(fetcher=...)` 에 픽스처를 주입해 조인·정렬·
경계 조건을 전부 검증한다 — 키가 들어올 때까지 이 코드가 검증되지 않은 채로
남는 것을 막기 위한 구조다.

이 파일이 지키는 주장은 넷이다.

1. **못 가져온 것과 없는 것은 다르다.** 키가 없으면 `available=false` 이지
   빈 목록이 아니다.
2. **공시 없는 값을 0 으로 접지 않는다.** 접으면 공시하지 않은 대출이 금리 0% 로
   1위가 된다.
3. **정렬 입력에 제휴·수수료가 없다.** 기준을 공개하고 그 목록을 테스트가 고정한다.
4. **외국인 가입 가능 여부를 만들어 내지 않는다.** 원문에 없는 정보다.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.tools import fss_client
from app.tools.fss_client import FssUnavailable, RawProducts, clear_cache, fetch
from app.tools.product_compare import EXCLUDED_INPUTS, build_cards, sort_policy

TODAY = date(2026, 8, 21)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """★ 테스트를 주변 `.env` 에서 격리한다.

    개발 머신에 실키가 들어오자 "키 없음" 테스트가 깨졌다(2026-08-21). 테스트가
    환경에 따라 다른 결과를 내면 그 순간 회귀 방어망이 아니게 된다 — 키를 명시적으로
    비우고 시작하고, 필요한 테스트만 `_fetch()` 로 넣는다.
    """
    monkeypatch.setenv("FSS_API_KEY", "")
    get_settings.cache_clear()
    clear_cache()
    yield
    clear_cache()
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def _deposit_payload() -> dict:
    """예금 2종. B 는 12개월 공시가 없어 **비교 대상에서 빠져야** 한다."""
    return {
        "result": {
            "err_cd": "000",
            "baseList": [
                {"dcls_month": "202608", "fin_co_no": "0010001", "fin_prdt_cd": "A",
                 "kor_co_nm": "가나은행", "fin_prdt_nm": "가나예금", "join_way": "영업점",
                 "join_deny": "1", "join_member": "실명의 개인", "etc_note": "",
                 "max_limit": "50000000"},
                {"dcls_month": "202608", "fin_co_no": "0010002", "fin_prdt_cd": "B",
                 "kor_co_nm": "다라은행", "fin_prdt_nm": "다라예금", "join_way": "인터넷",
                 "join_deny": "3", "join_member": "제한없음", "etc_note": "",
                 "max_limit": ""},
            ],
            "optionList": [
                {"fin_co_no": "0010001", "fin_prdt_cd": "A", "save_trm": "12",
                 "intr_rate_type_nm": "단리", "intr_rate": "3.0", "intr_rate2": "3.5"},
                {"fin_co_no": "0010001", "fin_prdt_cd": "A", "save_trm": "24",
                 "intr_rate_type_nm": "단리", "intr_rate": "3.2", "intr_rate2": "3.9"},
                {"fin_co_no": "0010002", "fin_prdt_cd": "B", "save_trm": "24",
                 "intr_rate_type_nm": "단리", "intr_rate": "3.4", "intr_rate2": "4.1"},
            ],
        }
    }


def _loan_payload() -> dict:
    """대출 2종. **실제 응답 형태를 그대로 흉내낸다.**

    상품마다 금리가 4종류(A 대출금리 / B 기준 / C 가산 / D 가감조정) 들어온다.
    구성요소를 거르지 않으면 가감조정금리(0.5%)가 "대출금리"로 표시된다.
    Y 는 A 의 평균금리 공시가 없다(`""`) — 0% 로 접으면 1위가 된다.
    """
    return {
        "result": {
            "err_cd": "000",
            "baseList": [
                {"dcls_month": "202608", "fin_co_no": "0020001", "fin_prdt_cd": "X",
                 "kor_co_nm": "마바은행", "fin_prdt_nm": "마바대출", "join_way": "영업점"},
                {"dcls_month": "202608", "fin_co_no": "0020002", "fin_prdt_cd": "Y",
                 "kor_co_nm": "사아은행", "fin_prdt_nm": "사아대출", "join_way": "인터넷"},
            ],
            "optionList": [
                {"fin_co_no": "0020001", "fin_prdt_cd": "X", "crdt_lend_rate_type": "A",
                 "crdt_lend_rate_type_nm": "대출금리", "crdt_grad_avg": "6.5"},
                {"fin_co_no": "0020001", "fin_prdt_cd": "X", "crdt_lend_rate_type": "B",
                 "crdt_lend_rate_type_nm": "기준금리", "crdt_grad_avg": "3.6"},
                {"fin_co_no": "0020001", "fin_prdt_cd": "X", "crdt_lend_rate_type": "D",
                 "crdt_lend_rate_type_nm": "가감조정금리", "crdt_grad_avg": "0.53"},
                {"fin_co_no": "0020002", "fin_prdt_cd": "Y", "crdt_lend_rate_type": "A",
                 "crdt_lend_rate_type_nm": "대출금리", "crdt_grad_avg": ""},
                {"fin_co_no": "0020002", "fin_prdt_cd": "Y", "crdt_lend_rate_type": "D",
                 "crdt_lend_rate_type_nm": "가감조정금리", "crdt_grad_avg": "0.31"},
            ],
        }
    }


def _fetch(kind, payload, key="test-key"):
    get_settings.cache_clear()
    import os

    os.environ["FSS_API_KEY"] = key
    try:
        return fetch(kind, today=TODAY, fetcher=lambda url, params, timeout: payload)
    finally:
        os.environ.pop("FSS_API_KEY", None)
        get_settings.cache_clear()


class TestUnavailableIsNotEmpty:
    def test_no_key_raises_unavailable(self):
        get_settings.cache_clear()
        with pytest.raises(FssUnavailable) as e:
            fetch("deposit", today=TODAY, fetcher=lambda *a: {})
        assert "FSS_API_KEY" in e.value.reason

    def test_error_code_is_not_treated_as_empty(self):
        payload = {"result": {"err_cd": "020", "err_msg": "일일 허용횟수 초과"}}
        with pytest.raises(FssUnavailable) as e:
            _fetch("deposit", payload)
        assert "020" in e.value.reason

    def test_endpoint_returns_200_with_available_false(self, client):
        """★ 502 도 빈 목록도 아니다. 둘 다 사실이 아닌 화면을 만든다."""
        get_settings.cache_clear()
        r = client.get("/api/v1/products", params={"kind": "deposit"})
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["results"] == []
        # 기준은 조회 실패와 무관하게 공개된다
        assert body["sort_policy"]["excluded_inputs"]


class TestJoinAndSort:
    def test_save_trm_filter_drops_products_without_that_term(self):
        raw = _fetch("deposit", _deposit_payload())
        cards = build_cards(raw, save_trm="12")
        assert [c.fin_prdt_cd for c in cards] == ["A"], "12개월 공시가 없는 B 는 빠져야 한다"

    def test_deposit_sorted_by_best_rate_desc(self):
        raw = _fetch("deposit", _deposit_payload())
        cards = build_cards(raw, save_trm="24")
        assert [c.fin_prdt_cd for c in cards] == ["B", "A"]
        assert cards[0].sort_value == 4.1

    def test_loan_uses_actual_rate_not_its_components(self):
        """★★ 가감조정금리를 "대출금리"로 표시하면 안 된다.

        `optionList` 는 상품당 A(대출금리)·B(기준)·C(가산)·D(가감조정)를 모두
        보낸다. 구분 없이 최저값을 고르면 **0.53% 짜리 가감조정금리가 대출금리로
        표시된다.** 실측(2026-08-21 실키)에서 상위 3건이 0.01%·0.02%·0.1% 로
        나왔는데 실재하지 않는 신용대출 금리다 — 공시 없는 값을 0 으로 접는 것과
        같은 종류이고, 그럴듯해 보여서 더 나쁘다.
        """
        raw = _fetch("credit_loan", _loan_payload())
        card = {c.fin_prdt_cd: c for c in build_cards(raw)}["X"]
        assert card.sort_value == 6.5, "대출금리(A) 여야 한다"
        assert all(o.rate_type == "대출금리" for o in card.options), (
            "구성요소(기준·가산·가감조정)가 화면에 금리로 나가면 안 된다"
        )

    def test_undisclosed_loan_rate_goes_last_not_first(self):
        """★ 이 테스트가 F7 에서 가장 중요하다.

        평균금리가 `""` 인 상품을 0.0 으로 접으면 **금리 0% 대출**이 되어
        목록 1위가 된다. 공시하지 않은 것이 최저금리가 되는 화면은 거짓이다.
        """
        raw = _fetch("credit_loan", _loan_payload())
        cards = build_cards(raw)
        assert [c.fin_prdt_cd for c in cards] == ["X", "Y"]
        assert cards[0].sort_value == 6.5
        assert cards[1].sort_value is None


class TestNeutrality:
    def test_sort_input_is_rate_only(self):
        for kind in ("deposit", "saving", "credit_loan"):
            p = sort_policy(kind)
            assert p["sort_input"] in ("intr_rate2", "crdt_grad_avg")

    def test_affiliate_and_fee_are_excluded(self):
        """기획서 14.3 — 제휴·수수료가 정렬 입력에 없다는 것을 코드로 고정한다."""
        for term in ("제휴 여부", "광고비", "수수료 수취"):
            assert term in EXCLUDED_INPUTS

    def test_policy_endpoint_matches_module(self, client):
        r = client.get("/api/v1/products/sort-policy", params={"kind": "credit_loan"})
        assert r.status_code == 200
        assert r.json() == sort_policy("credit_loan")


class TestForeignerEligibility:
    def test_no_eligibility_field_is_invented(self):
        """원문에 없는 정보를 필드로 만들지 않는다."""
        raw = _fetch("deposit", _deposit_payload())
        card = build_cards(raw, save_trm="24")[0]
        fields = set(card.__slots__)
        for banned in ("foreigner_ok", "visa_eligible", "foreigner_eligibility"):
            assert banned not in fields

    def test_join_deny_is_passed_through_verbatim(self):
        """`join_deny=1`(제한없음)을 '외국인도 가능'으로 옮기지 않는다."""
        raw = _fetch("deposit", _deposit_payload())
        by_code = {c.fin_prdt_cd: c for c in build_cards(raw, save_trm="24")}
        assert by_code["A"].join_deny == "1"
        assert by_code["B"].join_deny == "3"

    def test_policy_states_eligibility_must_be_confirmed(self):
        assert "직접 확인" in sort_policy("deposit")["foreigner_eligibility"]


class TestCache:
    def test_second_call_same_day_does_not_refetch(self):
        calls = {"n": 0}

        def fetcher(url, params, timeout):
            calls["n"] += 1
            return _deposit_payload()

        get_settings.cache_clear()
        import os

        os.environ["FSS_API_KEY"] = "k"
        try:
            fetch("deposit", today=TODAY, fetcher=fetcher)
            fetch("deposit", today=TODAY, fetcher=fetcher)
        finally:
            os.environ.pop("FSS_API_KEY", None)
            get_settings.cache_clear()
        assert calls["n"] == 1

    def test_next_day_refetches(self):
        calls = {"n": 0}

        def fetcher(url, params, timeout):
            calls["n"] += 1
            return _deposit_payload()

        get_settings.cache_clear()
        import os

        os.environ["FSS_API_KEY"] = "k"
        try:
            fetch("deposit", today=TODAY, fetcher=fetcher)
            fetch("deposit", today=date(2026, 8, 22), fetcher=fetcher)
        finally:
            os.environ.pop("FSS_API_KEY", None)
            get_settings.cache_clear()
        assert calls["n"] == 2

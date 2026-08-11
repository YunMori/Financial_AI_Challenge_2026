"""숫자 정규화 테스트 (planner §7.4 — 20케이스 필수).

숫자 대조 검사 전체가 이 함수 하나에 걸려 있다. 여기가 틀리면
"100만원 → 300만원" 같은 환각이 그대로 통과하거나, 반대로 정상 답변이
전부 차단된다.
"""

import pytest

from app.util.numerals import extract_numerals, numeral_supported, parse_numeral


class TestParseNumeral:
    @pytest.mark.parametrize(
        "token,expected",
        [
            # ── 같은 값의 여러 표기 (핵심) ────────────────────────────
            ("1,000,000원", "1000000:krw"),
            ("1000000원", "1000000:krw"),
            ("100만원", "1000000:krw"),
            ("100만 원", "1000000:krw"),
            ("백만원", "1000000:krw"),
            ("1백만원", "1000000:krw"),
            ("일백만원", "1000000:krw"),
            # ── 단위 구분 (5만 달러 ≠ 5만 원) ──────────────────────────
            ("5만 달러", "50000:usd"),
            ("5만원", "50000:krw"),
            ("50,000달러", "50000:usd"),
            ("$5,000", "5000:usd"),
            # ── 한도제한계좌 실제 수치 ────────────────────────────────
            ("30만원", "300000:krw"),
            ("300만원", "3000000:krw"),
            ("3백만원", "3000000:krw"),
            # ── 단위 없음 ─────────────────────────────────────────────
            ("100만", "1000000"),
            ("6", "6"),
            # ── 기타 단위 ─────────────────────────────────────────────
            ("30%", "30:pct"),
            ("6개 은행", "6:count"),
            ("3개월", "3:month"),
            ("2년", "2:year"),
            # ── 날짜 ──────────────────────────────────────────────────
            ("2024-05-02", "20240502:date"),
            ("2024.5.2", "20240502:date"),
            ("2024년 5월 2일", "20240502:date"),
            ("'24.5.2", "20240502:date"),
            ("2025-03-21", "20250321:date"),
        ],
    )
    def test_canonical_form(self, token, expected):
        n = parse_numeral(token)
        assert n is not None, f"{token!r} 이 파싱되지 않았습니다"
        assert str(n) == expected

    @pytest.mark.parametrize("token", ["", "   ", "재직증명서", "외국인등록증", "해당 없음"])
    def test_non_numeric_returns_none(self, token):
        assert parse_numeral(token) is None

    def test_same_value_different_notation_agree(self):
        """이 함수의 존재 이유 — 표기가 달라도 같은 값이어야 한다."""
        forms = ["1,000,000원", "100만원", "백만원", "1백만 원", "일백만원"]
        assert len({str(parse_numeral(f)) for f in forms}) == 1

    def test_different_units_do_not_collide(self):
        assert str(parse_numeral("5만 달러")) != str(parse_numeral("5만원"))


class TestExtractNumerals:
    def test_extracts_from_evidence_sentence(self):
        text = "’24.5.2.(목)부터 한도제한 계좌의 1일 거래한도가 30만원에서 100만원으로 상향된다."
        found = extract_numerals(text)
        assert "300000:krw" in found
        assert "1000000:krw" in found
        assert "20240502:date" in found

    def test_unitless_variant_is_also_registered(self):
        """근거에 '100만원', 답변에 '100만' 인 경우를 허용한다."""
        found = extract_numerals("한도가 100만원입니다")
        assert "1000000:krw" in found and "1000000" in found

    def test_table_row_extraction(self):
        text = "| 인터넷뱅킹 | 30만원 | 100만원 |\n| 창구 | 100만원 | 300만원 |"
        found = extract_numerals(text)
        assert {"300000:krw", "1000000:krw", "3000000:krw"} <= found


class TestNumeralSupported:
    @pytest.fixture
    def evidence(self):
        return extract_numerals(
            "’24.5.2.(목)부터 한도제한 계좌를 보유한 고객은 하루에 인터넷뱅킹 100만원 "
            "ATM 100만원 창구거래 300만원까지 거래할 수 있게 된다."
        )

    def test_supported_number_passes(self, evidence):
        assert numeral_supported("100만원", evidence)

    def test_notation_variant_passes(self, evidence):
        """답변이 '1,000,000원'으로 써도 근거의 '100만원'과 같은 값이다."""
        assert numeral_supported("1,000,000원", evidence)
        assert numeral_supported("백만원", evidence)

    def test_hallucinated_number_is_caught(self, evidence):
        """★ 환각 방어의 핵심 — 근거에 없는 300만원 대신 500만원."""
        assert not numeral_supported("500만원", evidence)

    def test_swapped_number_is_caught(self, evidence):
        """100만 → 200만 으로 바뀐 환각."""
        assert not numeral_supported("200만원", evidence)

    def test_wrong_unit_is_caught(self, evidence):
        """금액을 달러로 바꾸는 것은 완전히 다른 안내다."""
        assert not numeral_supported("100만 달러", evidence)

    def test_unparseable_token_is_allowed(self, evidence):
        """수치가 아닌 항목이 numbers_used 에 섞여도 정상 답변을 차단하지 않는다.

        거짓 폴백을 늘리는 쪽이 거짓 생성보다 낫다는 원칙의 예외 —
        여기서는 애초에 검사 대상이 아니다.
        """
        assert numeral_supported("재직증명서", evidence)

    def test_date_supported(self):
        ev = extract_numerals("2025. 3. 21. (금) 부터 6개 은행에서 이용할 수 있습니다")
        assert numeral_supported("2025-03-21", ev)
        assert numeral_supported("2025년 3월 21일", ev)

    def test_wrong_date_is_caught(self):
        ev = extract_numerals("2025. 3. 21. (금) 부터 이용할 수 있습니다")
        assert not numeral_supported("2025-03-22", ev)

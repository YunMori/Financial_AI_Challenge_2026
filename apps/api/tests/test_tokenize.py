"""토크나이저 테스트.

E-9 / E-7 혼동 방지는 이 서비스 검색 설계의 핵심 주장이다(planner §6.3).
그 주장이 실제로 성립하는지를 여기서 고정한다.
"""

import pytest

from app.rag.tokenize import extract_visa_codes, normalize_visa, tokenize


class TestVisaExtraction:
    """체류자격 코드 추출.

    planner 원안의 `\\b([A-H]-\\d{1,2})\\b` 는 숫자 뒤에 한글이 붙으면
    (한글도 \\w 이므로) 경계가 성립하지 않아 매칭에 실패했다.
    아래 '한글 조사가 붙는' 케이스들이 그 회귀를 막는다.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            # 공백이 뒤따르는 경우 — 원안도 통과하던 케이스
            ("E-9 비자입니다", ["E9"]),
            ("F-6 결혼이민", ["F6"]),
            ("H-2 방문취업", ["H2"]),
            # ★ 한글 조사가 바로 붙는 경우 — 원안이 실패하던 케이스
            ("E-9인데요", ["E9"]),
            ("제가 D-2에요", ["D2"]),
            ("E-9의 요건", ["E9"]),
            ("E-9와 E-7", ["E9", "E7"]),
            # ★ E-9 / E-7 변별 — 검색 설계의 핵심 주장
            ("E-7과 E-9의 차이", ["E7", "E9"]),
            # 하이픈 없는 표기
            ("E9 비자 계좌개설", ["E9"]),
            ("d2 유학생", ["D2"]),
            # 두 자리 코드를 잘라 읽지 않아야 함
            ("D-10 구직비자", ["D10"]),
            ("D-4 일반연수", ["D4"]),
            # 중복 제거 + 등장 순서 유지
            ("E-9 이야기, 다시 E-9, 그리고 D-2", ["E9", "D2"]),
            # 오탐 방지
            ("TYPE-9 는 비자가 아닙니다", []),
            ("ABC-1 코드", []),
            ("비자 이야기", []),
            ("", []),
        ],
    )
    def test_extraction(self, text, expected):
        assert extract_visa_codes(text) == expected

    def test_normalize_forms_agree(self):
        """문서는 'E-9', 이용자는 'E9' 로 쓴다. 같은 토큰이 되어야 한다."""
        assert normalize_visa("e-9") == normalize_visa("E9") == "E9"

    def test_document_and_query_forms_match(self):
        """색인 측(문서)과 질의 측이 같은 토큰을 낸다."""
        doc = tokenize("E-9(비전문취업) 체류자격 소지자의 계좌개설 요건")
        query = tokenize("E9 비자 계좌개설 되나요")
        assert "E9" in doc and "E9" in query


class TestCompoundNouns:
    """복합명사 보호.

    쪼개지면 '한도 제한 있는 계좌' 류의 무관한 문서까지 매칭되어 변별력이 사라진다.
    """

    @pytest.mark.parametrize(
        "text,term",
        [
            ("한도제한계좌를 해제하려면", "한도제한계좌"),
            ("지정거래은행 지정 없이", "지정거래은행"),
            ("외국인등록증으로 계좌개설", "외국인등록증"),
            ("해외송금이 가능한가요", "해외송금"),
            ("거래목적확인 서류", "거래목적확인"),
            ("재직증명서와 근로계약서", "재직증명서"),
            ("보이스피싱 피해구제신청", "보이스피싱"),
        ],
    )
    def test_domain_term_survives(self, text, term):
        assert term in tokenize(text)

    def test_split_would_lose_discrimination(self):
        """사용자 사전이 없으면 쪼개진다는 사실 자체를 고정한다."""
        from kiwipiepy import Kiwi

        plain = [t.form for t in Kiwi().tokenize("한도제한계좌")]
        assert plain != ["한도제한계좌"], "기본 사전이 이미 보존한다면 사용자 사전 항목을 재검토할 것"
        assert tokenize("한도제한계좌") == ["한도제한계좌"]


class TestTokenize:
    def test_drops_particles_and_endings(self):
        """조사·어미는 검색에 기여하지 않으므로 제거된다."""
        toks = tokenize("한도제한계좌를 해제하려면 어떤 서류가 필요한가요")
        assert "를" not in toks and "가" not in toks
        assert {"한도제한계좌", "해제", "서류"} <= set(toks)

    def test_visa_codes_appended_after_morphemes(self):
        assert tokenize("E-9인데 한도제한계좌를 해제하려면?") == [
            "한도제한계좌",
            "해제",
            "E9",
        ]

    def test_empty_input(self):
        assert tokenize("") == []

    def test_english_query_keeps_latin_tokens(self):
        """다국어 질의가 정규화되기 전에 들어와도 죽지 않아야 한다."""
        toks = tokenize("Limited-Purpose Account for E-9 holders")
        assert "E9" in toks
        assert any(t.lower() == "account" for t in toks)

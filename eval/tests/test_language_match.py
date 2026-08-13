"""출력 언어 일치 판정.

★ 이 검사는 원래 어디에도 없었다. `postprocess.finalize()` 는 `lang` 을 받지만
폴백 문구와 고지 삽입에만 쓴다 — **베트남어 질문에 한국어로 답해도 인용·숫자·
금지표현 검사를 전부 통과한다.** Claude 가 지시를 따랐으니 드러나지 않았을
뿐이고, 소형 모델의 전형적 실패가 바로 언어 이탈이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metrics import detect_answer_language  # noqa: E402

KO = "한도제한계좌는 하루 인터넷뱅킹 100만원까지 이체할 수 있습니다. 한도를 풀려면 거래 목적을 증명하는 서류가 필요합니다."
EN = "A limited-transaction account allows transfers up to 1,000,000 won per day via internet banking. You need documents proving your transaction purpose."
VI = "Tài khoản hạn chế giao dịch cho phép chuyển tối đa 1.000.000 won mỗi ngày. Bạn cần giấy tờ chứng minh mục đích giao dịch."

# 시스템 프롬프트가 en·vi 답변에 한국어 원어 괄호 병기를 지시한다.
EN_WITH_KO = ("You need a Certificate of Employment (재직증명서) or an Employment "
              "Contract (근로계약서) to lift the limit on your account at the bank counter.")
VI_WITH_KO = ("Bạn cần Giấy xác nhận công tác (재직증명서) để gỡ bỏ hạn mức của "
              "tài khoản hạn chế giao dịch tại quầy ngân hàng.")


class TestCorrectLanguage:
    @pytest.mark.parametrize("text,lang", [(KO, "ko"), (EN, "en"), (VI, "vi")])
    def test_matching(self, text, lang):
        assert detect_answer_language(text, lang) is True

    @pytest.mark.parametrize("text,lang", [(EN_WITH_KO, "en"), (VI_WITH_KO, "vi")])
    def test_korean_gloss_is_allowed(self, text, lang):
        """★ "한글 없음"으로 판정하면 정상 답변이 전부 실패한다.

        프롬프트가 한국어 원어 병기를 **지시**하므로 en·vi 정상 답변에도
        한글이 섞인다. 비율로 봐야 한다.
        """
        assert detect_answer_language(text, lang) is True


class TestLanguageDrift:
    """모델이 요청 언어를 벗어난 경우 — 이걸 잡는 것이 목적이다."""

    def test_korean_answer_to_vietnamese_question(self):
        assert detect_answer_language(KO, "vi") is False

    def test_korean_answer_to_english_question(self):
        assert detect_answer_language(KO, "en") is False

    def test_vietnamese_answer_to_english_question(self):
        """라틴 알파벳만 보면 en 과 vi 가 구분되지 않는다 — 고유자로 가른다."""
        assert detect_answer_language(VI, "en") is False

    def test_english_answer_to_korean_question(self):
        assert detect_answer_language(EN, "ko") is False


class TestUnjudgeable:
    """폴백 문구·빈 답변은 판정 대상이 아니다 — 지표를 오염시키면 안 된다."""

    @pytest.mark.parametrize("text", ["", "   ", "확인되지 않았습니다"])
    def test_too_short_returns_none(self, text):
        assert detect_answer_language(text, "ko") is None

    def test_unknown_language_returns_none(self):
        assert detect_answer_language(KO, "th") is None

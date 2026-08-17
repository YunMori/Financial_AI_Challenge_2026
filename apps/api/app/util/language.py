"""출력 언어 판정 — 답변이 요청 언어로 쓰였는가.

★ **이 검사는 원래 채점기에만 있었다.** `eval/metrics.py` 가 지표로 재기는 했지만
`postprocess.finalize()` 는 `lang` 을 폴백 문구와 계층 B 고지 삽입에만 썼다. 즉
**베트남어 질문에 한국어로 답해도 인용·숫자·금지표현 검사를 전부 통과해 이용자에게
나갔다.** Claude 는 지시를 따랐으니 드러나지 않았을 뿐이고, 언어 이탈은 소형 모델의
전형적 실패다 — `citations` 생략과 같은 부류(모델의 선의에 기대던 자리)다.

**채점기와 런타임이 같은 함수를 써야 한다.** 판정이 두 벌이면 "측정은 되는데 제품은
안 막는" 상태가 되거나, 언젠가 둘이 어긋난다. `eval/metrics.py` 가 이 모듈을 임포트한다.

스크립트(문자 체계) 기반이다 — 모델을 더 쓰지 않는다. 채점기가 채점 대상과 같은
종류의 실패를 하면 지표를 믿을 수 없기 때문이고, 런타임에서는 생성 경로에 LLM 호출을
하나 더 얹지 않기 위해서다.
"""

from __future__ import annotations

import re
import unicodedata

_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
# 베트남어 고유자(완성형). 라틴 알파벳만으로는 en 과 구분되지 않는다.
_VIET = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯ]")

# ⚠ "한글이 없어야 한다"로 판정하면 **안 된다.** 시스템 프롬프트가 en·vi 답변에
#   "필요하면 한국어 원어를 괄호로 병기"하도록 지시한다(`app/llm/prompts.py`).
#   정상 답변에도 한글이 섞이므로 **비율**로 본다.
HANGUL_MAX_FOR_NON_KO = 0.30   # 병기 수준을 넘으면 한국어로 답한 것
HANGUL_MIN_FOR_KO = 0.20       # 한국어 답변이면 이보다는 한글이 많다

# 이보다 짧으면 판정하지 않는다. 폴백 문구·빈 답변·"네."류 한 마디는 문자 분포로
# 언어를 가릴 수 없고, 억지로 가리면 정상 답변을 막는다.
MIN_CHARS = 20


def detect_answer_language(text: str, expected: str) -> bool | None:
    """답변이 요청 언어로 쓰였는가. **판정 불가면 None** (True/False 가 아니다).

    호출부는 `is False` 로 검사해야 한다 — `not result` 로 쓰면 판정 불가(None)를
    실패로 취급해 짧은 정상 답변을 막는다.
    """
    stripped = "".join(ch for ch in (text or "") if not ch.isspace())
    if len(stripped) < MIN_CHARS:
        return None

    hangul_ratio = len(_HANGUL.findall(stripped)) / len(stripped)
    # 결합 성조가 분해돼 있어도 잡히도록 NFC 로 맞춘다.
    has_viet = bool(_VIET.search(unicodedata.normalize("NFC", text)))

    if expected == "ko":
        return hangul_ratio >= HANGUL_MIN_FOR_KO
    if expected == "vi":
        return has_viet and hangul_ratio <= HANGUL_MAX_FOR_NON_KO
    if expected == "en":
        return not has_viet and hangul_ratio <= HANGUL_MAX_FOR_NON_KO
    return None


__all__ = ["detect_answer_language", "HANGUL_MAX_FOR_NON_KO", "HANGUL_MIN_FOR_KO",
           "MIN_CHARS"]

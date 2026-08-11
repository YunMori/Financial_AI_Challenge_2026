"""BM25 색인·질의용 한국어 토크나이저.

한국어를 공백으로 자르면 "한도제한계좌를"과 "한도제한계좌"가 매칭되지 않는다.
kiwipiepy 형태소 분석으로 명사만 뽑되, 두 가지를 보정한다.

1. **복합명사 보호** — 기본 사전은 "한도제한계좌"를 한도/제한/계좌로 쪼갠다.
   쪼개지면 "한도 제한 있는 계좌"류의 무관한 문서까지 매칭되어 변별력이 사라진다.
   도메인 용어를 고유명사(NNP)로 등록해 한 덩어리로 유지한다.

2. **체류자격 코드 보호** — 형태소 분석기는 "E-9"를 E/9 로 깨뜨린다.
   임베딩만으로는 E-9 와 E-7 이 거의 같은 벡터가 되므로, BM25 가 두 코드를
   서로 다른 토큰으로 취급하는 것이 혼동 방지의 핵심이다(planner §6.3).
   따라서 형태소 분석 **전에** 정규식으로 추출해 보호한다.

색인과 질의가 반드시 같은 함수를 통과해야 한다. 한쪽만 사용자 사전을 쓰면
토큰이 어긋나 BM25 가 조용히 무력화된다.
"""

from __future__ import annotations

import re
from functools import lru_cache

from kiwipiepy import Kiwi

# 고유명사로 보호할 도메인 용어.
# 코퍼스를 넓히면서 계속 추가한다 — 새 용어를 넣을 때는 반드시
# tests/test_tokenize.py 에 케이스를 함께 추가할 것.
DOMAIN_TERMS: tuple[str, ...] = (
    # 계좌 · 한도
    "한도제한계좌",
    "거래목적확인",
    "계좌개설",
    "비대면계좌개설",
    "금융거래목적",
    # 신분 · 체류
    "외국인등록증",
    "모바일외국인등록증",
    "외국인등록번호",
    "체류자격",
    "체류기간",
    "재직증명서",
    "근로계약서",
    "급여명세서",
    "재학증명서",
    # 송금 · 외환
    "해외송금",
    "지정거래은행",
    "해외송금통합관리시스템",
    "외국환거래규정",
    "매매기준율",
    # 사기 · 피해구제
    "보이스피싱",
    "전기통신금융사기",
    "피해구제신청",
    "지급정지",
)

# 체류자격 코드. 뒤쪽 \b 를 쓰면 "E-9인데" / "E-7과" 처럼 숫자 뒤에 한글이 붙는
# 가장 자연스러운 한국어 어순에서 매칭이 실패한다(한글도 \w 이므로 경계가 없음).
# 대신 앞뒤를 명시적 부정탐색으로 막는다.
#   (?<![A-Za-z0-9])  "TYPE-9" 의 E 처럼 단어 중간을 잡지 않음
#   -?                "E-9" 와 "E9" 를 모두 허용
#   (?![0-9])         "D-10" 을 D-1 로 잘라 읽지 않음
VISA_RE = re.compile(r"(?<![A-Za-z0-9])([A-H]-?\d{1,2})(?![0-9])", re.IGNORECASE)

# 명사류 + 외국어(SL) + 숫자(SN) 만 남긴다. 조사·어미는 검색에 기여하지 않는다.
KEEP_TAGS = frozenset({"NNG", "NNP", "SL", "SN"})


@lru_cache(maxsize=1)
def _kiwi() -> Kiwi:
    """사용자 사전이 적재된 Kiwi 인스턴스 (프로세스당 1회 생성)."""
    kiwi = Kiwi()
    for term in DOMAIN_TERMS:
        kiwi.add_user_word(term, "NNP")
    return kiwi


def normalize_visa(code: str) -> str:
    """체류자격 코드를 하이픈 없는 대문자로 정규화한다.

    문서는 "E-9", 이용자는 "E9" 로 쓰는 일이 흔하다. 둘이 같은 토큰이 되어야
    BM25 에서 매칭된다.

    >>> normalize_visa("e-9"), normalize_visa("E9")
    ('E9', 'E9')
    """
    return code.upper().replace("-", "")


def extract_visa_codes(text: str) -> list[str]:
    """텍스트에서 체류자격 코드를 등장 순서대로, 중복 없이 뽑는다."""
    seen: dict[str, None] = {}
    for match in VISA_RE.findall(text):
        seen.setdefault(normalize_visa(match), None)
    return list(seen)


def tokenize(text: str) -> list[str]:
    """BM25 색인·질의용 토큰 목록.

    >>> tokenize("E-9인데 한도제한계좌를 해제하려면?")
    ['한도제한계좌', '해제', 'E9']
    """
    if not text:
        return []

    visas = extract_visa_codes(text)
    # 코드를 공백으로 치환해 형태소 분석기가 손대지 못하게 한다.
    stripped = VISA_RE.sub(" ", text)
    morphs = [t.form for t in _kiwi().tokenize(stripped) if t.tag in KEEP_TAGS]
    return morphs + visas

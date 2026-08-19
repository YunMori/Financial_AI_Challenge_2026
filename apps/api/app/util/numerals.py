"""숫자·날짜 정규화 (planner §7.4).

**이 서비스에서 가장 실용적인 환각 방어가 여기에 걸려 있다.** 금융 안내의
치명적 오류는 대부분 숫자에서 나온다 — 100만 원을 300만 원으로 바꾸는 환각은
문장이 아무리 자연스러워도 이용자를 창구에서 헛걸음시킨다.

모델이 신고한 `numbers_used` 의 모든 항목이 근거 텍스트에 실재하는지 대조하는데,
표기가 다르면 같은 값도 다르게 보인다:

    "1,000,000원"  "100만원"  "백만원"  "1백만 원"  → 전부 같은 값

그래서 양쪽을 정규형으로 바꾼 뒤 비교한다. 단위는 버리지 않는다 —
"5만 달러"와 "5만 원"은 완전히 다른 안내다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 한자어 수사. "일백만" 같은 표기를 위해.
SINO_DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4,
               "오": 5, "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
# 자릿수 배수. 큰 것부터 처리해야 "억"이 "만"보다 먼저 적용된다.
MULTIPLIERS: tuple[tuple[str, int], ...] = (
    ("조", 10**12), ("억", 10**8), ("만", 10**4),
    ("천", 10**3), ("백", 10**2), ("십", 10),
)

# 영어·베트남어 배수어. **다국어 서비스이므로 답변이 한국어 표기로만 오지
# 않는다.** 이걸 모르면 "30 million won" 이 30 으로 읽혀 근거와 대조되고,
# 정상 답변이 "근거 없는 수치"로 차단된다(실측: exp_003 과잉폴백 9건 중 다수).
WESTERN_MULTIPLIERS: dict[str, int] = {
    "thousand": 10**3, "million": 10**6, "billion": 10**9, "trillion": 10**12,
    "nghìn": 10**3, "ngàn": 10**3, "triệu": 10**6, "tỷ": 10**9, "ty": 10**9,
}
WESTERN_MULT_PATTERN = "|".join(
    sorted((re.escape(w) for w in WESTERN_MULTIPLIERS), key=len, reverse=True)
)

# 단위 정규화. 표기가 달라도 같은 단위로 묶는다.
UNIT_ALIASES: dict[str, str] = {
    "원": "krw", "won": "krw", "krw": "krw",
    "동": "vnd", "đồng": "vnd", "dong": "vnd", "vnd": "vnd",
    "달러": "usd", "불": "usd", "dollar": "usd", "dollars": "usd", "usd": "usd", "$": "usd",
    "%": "pct", "퍼센트": "pct", "프로": "pct",
    "년": "year", "개월": "month", "달": "month", "일": "day", "회": "count", "건": "count",
    "명": "person", "곳": "place", "개": "count",
}
UNIT_PATTERN = "|".join(sorted((re.escape(u) for u in UNIT_ALIASES), key=len, reverse=True))

# 날짜: 2024-05-02 / 2024.5.2 / 2024년 5월 2일 / '24.5.2 / 24.5.2.
DATE_RE = re.compile(
    r"(?<![\d.])'?(\d{2,4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?(?![\d])"
)
# 연월만: 2024년 5월 / 2024-05
YEARMONTH_RE = re.compile(r"(?<![\d.])'?(\d{2,4})\s*[.\-/년]\s*(\d{1,2})\s*월(?![\d])")

# 연도가 **뒤에** 오는 표기: 29/3/2024 (베트남·유럽) / 3/29/2024 (미국).
# 한국 문서는 연도가 앞이지만 **답변은 en·vi 로도 나간다.** 이걸 모르면
# "29/3/2024" 가 날짜가 아니라 수 29 로 읽혀 근거와 대조된다.
TRAILING_YEAR_RE = re.compile(
    r"(?<![\d.])(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4})(?![\d])"
)

# 수량: 선택적 통화기호 + 숫자(또는 한자어 수사) + 배수 + 선택적 단위
#   1,000,000원 / 100만원 / 백만 원 / $5,000 / 5만 달러 / 30% / 6개
#
# **뒤쪽 경계(`(?![\w])`)를 쓰지 않는다.** 한국어는 조사가 바로 붙어
# ("100만원입니다", "30만원에서") 경계가 성립하지 않는다. 토크나이저의
# 체류자격 정규식에서 똑같이 겪은 문제다(dev-log 2026-08-11).
# 앞쪽만 숫자·구분자로 막아 수를 중간에서 자르는 것을 방지한다.
# `num` 의 첫 대안이 **유럽·베트남식 천단위 점**이다(3.000.000). 반드시 먼저
# 와야 한다 — 뒤에 두면 `\d[\d,]*(?:\.\d+)?` 가 "3.000" 을 소수 3.0 으로 먹고
# 나머지를 흘린다. 실제로 그랬다: "3.000.000 won" 이 **3** 으로 읽혔다.
# 1,000,000 배 틀린 값이 조용히 통과할 수 있는 자리였다.
QUANTITY_RE = re.compile(
    r"(?<![\d.,])"
    r"(?P<cur>[$₩])?\s*"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?![\d.])"
    r"|\d[\d,]*(?:\.\d+)?"
    r"|[영공일이삼사오육칠팔구십백천만억조]{2,8})"
    r"\s*(?P<mult>[십백천만억조]*)"
    r"\s*(?P<wmult>" + WESTERN_MULT_PATTERN + r")?"
    r"\s*(?P<unit>" + UNIT_PATTERN + r")?",
    re.IGNORECASE,
)
# 천단위 점 표기인지 판정한다. `3.000.000` 은 그렇고 `3.5` 는 아니다.
_DOT_GROUPED_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+$")

# 날짜로 이미 해석한 구간은 수량 스캔에서 제외한다. 그러지 않으면
# "2024.5.2" 가 2024 / 5 / 2 세 개의 수로도 잡혀 근거 집합이 오염된다.
_MASK = " "  # 길이를 보존해야 뒤이은 마스킹의 인덱스가 밀리지 않는다

# 한자어 수사는 **2자 이상**만 받는다. 한 글자를 허용하면 "이체"의 "이"가
# 2로, "만족"의 "만"이 10000으로 잡혀 근거 집합에 없는 수가 섞인다.
# 근거 쪽에 가짜 수가 늘면 환각을 통과시키게 되므로 보수적으로 간다.


@dataclass(frozen=True, slots=True)
class Numeral:
    """정규화된 수치 하나."""

    value: float
    unit: str  # "" 이면 단위 없음

    def __str__(self) -> str:
        v = int(self.value) if self.value == int(self.value) else self.value
        return f"{v}{':' + self.unit if self.unit else ''}"


def _sino_simple(text: str) -> int | None:
    """만/억/조가 섞인 수사를 왼쪽부터 누적 계산한다."""
    total = 0
    chunk = 0
    current = 0
    for ch in text:
        if ch in SINO_DIGITS:
            current = SINO_DIGITS[ch]
        elif ch in ("십", "백", "천"):
            chunk += (current or 1) * dict(MULTIPLIERS)[ch]
            current = 0
        elif ch in ("만", "억", "조"):
            chunk += current
            total += (chunk or 1) * dict(MULTIPLIERS)[ch]
            chunk = current = 0
    result = total + chunk + current
    return result or None


def _apply_multiplier(base: float, mult_text: str) -> float:
    for ch in mult_text:
        for name, factor in MULTIPLIERS:
            if ch == name:
                base *= factor
                break
    return base


def parse_numeral(token: str) -> Numeral | None:
    """단일 표기를 정규형으로. 수치가 아니면 None.

    >>> str(parse_numeral("100만원"))
    '1000000:krw'
    >>> str(parse_numeral("1,000,000 원"))
    '1000000:krw'
    >>> str(parse_numeral("5만 달러"))
    '50000:usd'
    """
    token = token.strip()
    if not token:
        return None
    if cands := _date_candidates(token):
        return Numeral(value=float(cands[0].replace("-", "")), unit="date")
    m = QUANTITY_RE.search(token)
    if not m:
        return None
    return _numeral_from_match(m)


def _numeral_from_match(m: re.Match[str]) -> Numeral | None:
    raw = m.group("num")
    if raw[0].isdigit():
        # `3.000.000` 의 점은 소수점이 아니라 천단위 구분자다.
        cleaned = raw.replace(".", "") if _DOT_GROUPED_RE.match(raw) else raw
        try:
            base = float(cleaned.replace(",", ""))
        except ValueError:
            return None
    else:
        parsed = _sino_simple(raw)
        if parsed is None:
            return None
        base = float(parsed)
    value = _apply_multiplier(base, m.group("mult") or "")
    if wmult := (m.group("wmult") or "").lower():
        value *= WESTERN_MULTIPLIERS[wmult]
    # 단위는 뒤(100만원)에도 앞($5,000)에도 올 수 있다.
    unit_raw = (m.group("unit") or m.group("cur") or "").lower()
    return Numeral(value=value, unit=UNIT_ALIASES.get(unit_raw, ""))


def _date_candidates(text: str) -> list[str]:
    """가능한 ISO 날짜 표기들.

    `29/3/2024` 처럼 연도가 뒤에 오면 **일/월 순서를 알 수 없다** — 베트남·유럽은
    일이 먼저, 미국은 월이 먼저다. 한쪽으로 찍으면 절반은 틀린다.
    그래서 **가능한 해석을 전부 낸다.** 근거에 하나라도 있으면 지지된 것으로 본다.
    앞 숫자가 12 를 넘으면 일이 확실하므로 후보는 하나뿐이다.
    """
    if d := _parse_date(text):
        return [d]
    if m := TRAILING_YEAR_RE.search(text):
        a, b, y = (int(g) for g in m.groups())
        out: list[str] = []
        if 1 <= b <= 12 and 1 <= a <= 31:  # a=일 b=월 (베트남·유럽)
            out.append(f"{y:04d}-{b:02d}-{a:02d}")
        if 1 <= a <= 12 and 1 <= b <= 31:  # a=월 b=일 (미국)
            iso = f"{y:04d}-{a:02d}-{b:02d}"
            if iso not in out:
                out.append(iso)
        return out
    return []


def _parse_date(text: str) -> str | None:
    """ISO 날짜 문자열로. 두 자리 연도는 2000년대로 본다."""
    if m := DATE_RE.search(text):
        y, mo, d = (int(g) for g in m.groups())
        y = y + 2000 if y < 100 else y
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    if m := YEARMONTH_RE.search(text):
        y, mo = (int(g) for g in m.groups())
        y = y + 2000 if y < 100 else y
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-00"
    return None


def extract_numerals(text: str) -> set[str]:
    """텍스트에 등장한 모든 수치의 정규형 집합.

    근거 텍스트 쪽에 쓴다. 답변의 `numbers_used` 가 이 집합에 포함되는지로
    숫자 대조 검사를 수행한다.
    """
    found: set[str] = set()

    for m in DATE_RE.finditer(text):
        y, mo, d = (int(g) for g in m.groups())
        y = y + 2000 if y < 100 else y
        if 1 <= mo <= 12 and 1 <= d <= 31:
            found.add(str(Numeral(float(f"{y:04d}{mo:02d}{d:02d}"), "date")))
    for m in YEARMONTH_RE.finditer(text):
        y, mo = (int(g) for g in m.groups())
        y = y + 2000 if y < 100 else y
        if 1 <= mo <= 12:
            found.add(str(Numeral(float(f"{y:04d}{mo:02d}00"), "date")))

    # 날짜로 해석한 구간을 지우고 수량을 스캔한다.
    masked = list(text)
    for pattern in (DATE_RE, YEARMONTH_RE):
        for m in pattern.finditer(text):
            masked[m.start():m.end()] = _MASK * (m.end() - m.start())
    scan = "".join(masked)

    for m in QUANTITY_RE.finditer(scan):
        if n := _numeral_from_match(m):
            found.add(str(n))
            if n.unit:
                # 근거에 "100만원", 답변에 "100만" 처럼 단위가 빠지는 경우를
                # 허용한다. 반대(답변에만 단위가 있는 경우)는 허용하지 않는다 —
                # 없는 단위를 붙이는 것이 더 위험하기 때문이다.
                found.add(str(Numeral(n.value, "")))
    return found


def _appears_in_text(token: str, text: str) -> bool:
    """`token` 이 근거 텍스트에 **눈에 보이는 형태로** 있는지.

    정규형 비교(`evidence` 집합)는 단위 등가성(`100만원` ≡ `1,000,000원`)을 잡는
    대신, **부분 표기를 놓친다.** 근거의 "100만원" 은 `1000000:krw` 로만 남으므로
    모델이 `numbers_used` 에 맨숫자 `100` 을 적으면 매칭되지 않는다. 날짜도
    `2024-05-02` → `20240502:date` 라서 `2024` 나 `5` 가 걸리지 않는다.

    실측 (2026-08-19 · exp_011): 차단된 20건을 근거 원문과 대조하니 **`1350`(근거에
    없는 전화번호)과 `100,000` 만 진짜였고 나머지는 전부 근거에 그대로 있었다.**

    가드가 물어야 할 질문은 "정규형이 일치하는가"가 아니라 **"모델이 지어냈는가,
    아니면 우리가 보여준 것인가"** 다. 그래서 다음 둘 중 하나면 인정한다.

    1. 표기 그대로 등장 (`2024-05-02`, `May 10th` 처럼 구분자가 있는 형태)
    2. 숫자열이 **독립된 수 토큰**으로 등장 — 앞뒤가 숫자가 아니어야 한다

    ★ 2의 경계 조건이 핵심이다. 단순 부분문자열이면 근거의 `1000000` 안에서
      `1350` 을 제외한 거의 모든 것이 매칭돼 가드가 무력해진다. 실제로 부분문자열
      방식은 20건 중 12건을 "해소"했는데 그중에 정탐도 섞여 있었다.
    """
    if not text:
        return False
    if token in text:
        return True
    digits = re.sub(r"\D", "", token)
    if not digits:
        return False
    # 자릿수 구분 쉼표·공백만 지우고 본다. 하이픈은 남긴다 — 날짜를 숫자열로
    # 뭉개면 `2023-08-08` 이 `20230808` 이 되어 경계 판정이 어긋난다.
    flat = re.sub(r"[,\s]", "", text)
    return re.search(rf"(?<!\d){re.escape(digits)}(?!\d)", flat) is not None


def numeral_supported(token: str, evidence: set[str], text: str = "") -> bool:
    """답변에 등장한 수치 표기가 근거에 실재하는지.

    파싱되지 않는 표기는 **지지된 것으로 본다.** 수치가 아닌 문자열
    (서류명 등)이 numbers_used 에 섞여 들어온 것을 근거 부족으로 오판하면
    정상 답변이 차단된다 — 거짓 폴백을 늘리는 쪽이 더 나쁘다.

    `text` 는 모델에게 실제로 보여준 근거 블록(`EvidenceContext.block`)이다.
    정규형 비교가 놓치는 부분 표기를 여기서 건진다 — `_appears_in_text` 참조.
    비워 두면 정규형 비교만 하므로 예전 동작과 같다.
    """
    # 날짜는 해석이 여럿일 수 있다(일/월 순서). 하나라도 근거에 있으면 인정한다.
    if cands := _date_candidates(token):
        if any(str(Numeral(float(c.replace("-", "")), "date")) in evidence for c in cands):
            return True
        return _appears_in_text(token, text)
    n = parse_numeral(token)
    if n is None:
        return True
    if str(n) in evidence:
        return True
    # 단위 없는 표기는 어떤 단위로든 근거에 있으면 인정한다.
    if not n.unit and any(
        e.split(":")[0] == str(Numeral(n.value, "")).split(":")[0] for e in evidence
    ):
        return True
    return _appears_in_text(token, text)

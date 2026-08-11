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

# 단위 정규화. 표기가 달라도 같은 단위로 묶는다.
UNIT_ALIASES: dict[str, str] = {
    "원": "krw", "won": "krw", "krw": "krw",
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

# 수량: 선택적 통화기호 + 숫자(또는 한자어 수사) + 배수 + 선택적 단위
#   1,000,000원 / 100만원 / 백만 원 / $5,000 / 5만 달러 / 30% / 6개
#
# **뒤쪽 경계(`(?![\w])`)를 쓰지 않는다.** 한국어는 조사가 바로 붙어
# ("100만원입니다", "30만원에서") 경계가 성립하지 않는다. 토크나이저의
# 체류자격 정규식에서 똑같이 겪은 문제다(dev-log 2026-08-11).
# 앞쪽만 숫자·구분자로 막아 수를 중간에서 자르는 것을 방지한다.
QUANTITY_RE = re.compile(
    r"(?<![\d.,])"
    r"(?P<cur>[$₩])?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?|[영공일이삼사오육칠팔구십백천만억조]{2,8})"
    r"\s*(?P<mult>[십백천만억조]*)"
    r"\s*(?P<unit>" + UNIT_PATTERN + r")?",
    re.IGNORECASE,
)

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
    if d := _parse_date(token):
        return Numeral(value=float(d.replace("-", "")), unit="date")
    m = QUANTITY_RE.search(token)
    if not m:
        return None
    return _numeral_from_match(m)


def _numeral_from_match(m: re.Match[str]) -> Numeral | None:
    raw = m.group("num")
    if raw[0].isdigit():
        try:
            base = float(raw.replace(",", ""))
        except ValueError:
            return None
    else:
        parsed = _sino_simple(raw)
        if parsed is None:
            return None
        base = float(parsed)
    value = _apply_multiplier(base, m.group("mult") or "")
    # 단위는 뒤(100만원)에도 앞($5,000)에도 올 수 있다.
    unit_raw = (m.group("unit") or m.group("cur") or "").lower()
    return Numeral(value=value, unit=UNIT_ALIASES.get(unit_raw, ""))


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


def numeral_supported(token: str, evidence: set[str]) -> bool:
    """답변에 등장한 수치 표기가 근거에 실재하는지.

    파싱되지 않는 표기는 **지지된 것으로 본다.** 수치가 아닌 문자열
    (서류명 등)이 numbers_used 에 섞여 들어온 것을 근거 부족으로 오판하면
    정상 답변이 차단된다 — 거짓 폴백을 늘리는 쪽이 더 나쁘다.
    """
    n = parse_numeral(token)
    if n is None:
        return True
    if str(n) in evidence:
        return True
    # 단위 없는 표기는 어떤 단위로든 근거에 있으면 인정한다.
    if not n.unit:
        return any(e.split(":")[0] == str(Numeral(n.value, "")).split(":")[0] for e in evidence)
    return False

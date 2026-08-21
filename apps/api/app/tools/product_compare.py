"""FSS 원문 → 비교 카드 (F7, planner §9.2 · 기획서 14.3 중립성).

`baseList`(상품) 와 `optionList`(금리 옵션)를 조인해 카드를 만든다.

★ **정렬 입력에 제휴·수수료가 들어가지 않는다.** 들어갈 수 있는 값은 공시 금리
  하나뿐이고, 그 사실을 `sort_policy()` 로 화면과 API 양쪽에 공개한다.
  F2 의 `ranking-policy` 와 같은 장치이며 이유도 같다 — 순위를 만드는 서비스가
  기준을 숨기면 그 순위가 광고와 구별되지 않는다.

★ **외국인 가입 가능 여부는 여기서 만들어 낼 수 없다.** 원문에 그 정보가 없다.
  `join_deny`·`join_member` 를 **그대로** 옮기고, 외국인 가입 여부는 unknown 으로
  둔다. "제한없음(join_deny=1)"을 "외국인도 됩니다"로 옮기는 순간 그것이 환각이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.tools.fss_client import ProductKind, RawProducts

# 정렬에 쓰는 값. **이 목록이 곧 공개되는 기준이다** (`sort_policy`).
SORT_INPUT = {
    "deposit": "intr_rate2",      # 최고우대금리 — 높을수록 위
    "saving": "intr_rate2",
    "credit_loan": "crdt_grad_avg",  # 평균금리 — 낮을수록 위
}

# 정렬에 **넣지 않는** 값. 기획서 14.3 의 "제휴·수수료 입력 없음"을 코드로 못박는다.
EXCLUDED_INPUTS = ("제휴 여부", "광고비", "수수료 수취", "은행 규모", "조회수")


def _num(v: object) -> float | None:
    """`""`, `None`, `"-"` 를 0.0 으로 접지 않는다.

    금리에서 0.0 은 "0% 다"이고 None 은 "공시가 없다"다. 접으면 공시 없는 상품이
    최저금리 대출로 1위가 된다 — F2 의 `unverified` 와 같은 함정이다.
    """
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Option:
    save_trm: str | None
    rate_type: str
    rate: float | None
    rate_max: float | None


@dataclass(frozen=True, slots=True)
class ProductCard:
    fin_co_no: str
    fin_prdt_cd: str
    company: str
    product: str
    join_way: str
    join_deny: str
    join_member: str
    etc_note: str
    max_limit: float | None
    options: tuple[Option, ...]
    sort_value: float | None
    dcls_month: str


def _key(row: dict) -> tuple[str, str]:
    return str(row.get("fin_co_no", "")), str(row.get("fin_prdt_cd", ""))


def _deposit_option(o: dict) -> Option:
    return Option(
        save_trm=str(o.get("save_trm") or "") or None,
        rate_type=str(o.get("intr_rate_type_nm") or ""),
        rate=_num(o.get("intr_rate")),
        rate_max=_num(o.get("intr_rate2")),
    )


# ★★ **신용대출 `optionList` 에는 상품당 금리가 4종류 들어온다.**
#
#      A  대출금리        ← 이용자가 실제로 내는 금리
#      B  기준금리        구성요소
#      C  가산금리        구성요소
#      D  가감조정금리    구성요소 (작다 — 0.5% 수준)
#
#    이걸 구분하지 않고 최저값을 고르면 **가감조정금리가 "대출금리"로 표시된다.**
#    실측(2026-08-21 실키): 상위 3건이 0.01% · 0.02% · 0.1% 로 나왔는데, 실재하지
#    않는 신용대출 금리다. 이용자가 "0.01% 대출"로 읽는 순간 이 화면은 거짓이다 —
#    공시 없는 값을 0 으로 접는 것과 같은 종류의 오류이고, 더 나쁘다(그럴듯해서).
#
#    → A 만 쓴다. 실측에서 41종 **전부** A 를 갖고 있어 손실이 없다.
LOAN_RATE_TYPE_ACTUAL = "A"


def _loan_option(o: dict) -> Option:
    return Option(
        save_trm=None,
        rate_type=str(o.get("crdt_lend_rate_type_nm") or ""),
        rate=_num(o.get("crdt_grad_avg")),
        rate_max=None,
    )


def build_cards(raw: RawProducts, save_trm: str | None = None) -> list[ProductCard]:
    """조인 → (선택) 기간 필터 → 정렬.

    `save_trm` 은 예·적금에만 의미가 있다. 기간을 고르지 않으면 상품마다 다른
    기간의 금리가 섞여 비교가 성립하지 않으므로, 화면은 기간을 먼저 고르게 한다.
    """
    is_loan = raw.kind == "credit_loan"
    parse = _loan_option if is_loan else _deposit_option

    by_key: dict[tuple[str, str], list[Option]] = {}
    for o in raw.option_list:
        if is_loan:
            # 구성요소(기준·가산·가감조정)를 대출금리로 오인하지 않는다 — 위 주석 참조.
            if str(o.get("crdt_lend_rate_type") or "") != LOAN_RATE_TYPE_ACTUAL:
                continue
        elif save_trm and str(o.get("save_trm") or "") != save_trm:
            continue
        by_key.setdefault(_key(o), []).append(parse(o))

    cards: list[ProductCard] = []
    for b in raw.base_list:
        k = _key(b)
        options = tuple(by_key.get(k, ()))
        if not options:
            # 선택한 기간의 금리가 없는 상품은 **비교 대상이 아니다**.
            # 남겨 두면 금리 칸이 빈 카드가 되고, 빈 칸은 "0%"로 읽힌다.
            continue
        if is_loan:
            values = [o.rate for o in options if o.rate is not None]
            sort_value = min(values) if values else None
        else:
            values = [o.rate_max for o in options if o.rate_max is not None]
            sort_value = max(values) if values else None
        cards.append(
            ProductCard(
                fin_co_no=k[0],
                fin_prdt_cd=k[1],
                company=str(b.get("kor_co_nm") or ""),
                product=str(b.get("fin_prdt_nm") or ""),
                join_way=str(b.get("join_way") or ""),
                join_deny=str(b.get("join_deny") or ""),
                join_member=str(b.get("join_member") or ""),
                etc_note=str(b.get("etc_note") or ""),
                max_limit=_num(b.get("max_limit")),
                options=options,
                sort_value=sort_value,
                dcls_month=str(b.get("dcls_month") or raw.dcls_month),
            )
        )

    # 공시가 없는 상품(sort_value=None)은 **맨 뒤**로 보낸다. 대출에서 None 을
    # 0.0 으로 접으면 "금리 0%"가 되어 1위가 된다.
    def order(c: ProductCard) -> tuple[int, float]:
        if c.sort_value is None:
            return (1, 0.0)
        return (0, c.sort_value if is_loan else -c.sort_value)

    cards.sort(key=order)
    return cards


def sort_policy(kind: ProductKind) -> dict:
    """정렬 기준 공개 (기획서 14.3). `matrix/fit.py::ranking_policy()` 와 같은 취지."""
    is_loan = kind == "credit_loan"
    return {
        "kind": kind,
        "sort_input": SORT_INPUT[kind],
        "direction": "asc" if is_loan else "desc",
        "excluded_inputs": list(EXCLUDED_INPUTS),
        "undisclosed_handling": (
            "공시 금리가 없는 상품은 0 으로 접지 않고 목록 맨 뒤에 둔다. "
            "0 으로 접으면 공시하지 않은 상품이 최저금리 대출로 1위가 된다."
        ),
        "foreigner_eligibility": (
            "이 API 는 외국인 가입 가능 여부를 제공하지 않는다. 상품 조건 비교이며 "
            "가입 가능 여부는 해당 금융기관에 직접 확인해야 한다."
        ),
    }

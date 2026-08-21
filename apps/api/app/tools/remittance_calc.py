"""송금 한도·환산 계산 (F6, planner §9.2).

★ **순수 함수다. LLM 이 개입하지 않는다** (planner §10-F6 3번). 금액 계산을
  생성 모델에 맡기면 숫자 대조(§8.3)로도 잡히지 않는 오류가 나온다 — 근거
  텍스트에 없는 값이 아니라 **계산이 틀린 값**이기 때문이다.

★ **한도는 이 모듈이 아니라 카탈로그가 갖는다** (`corpus/matrix/remittance.yaml`).
  지금 그 값은 전부 `unknown` 이며, 그 상태에서 이 모듈은 **숫자를 만들어 내지
  않는다** — 남은 한도를 계산할 수 없으면 `None` 을 돌려주고 화면이 "확인 필요"로
  그린다. 기획서의 5만/5천 달러를 기본값으로 넣지 않는 이유는 `remittance.yaml`
  머리말에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import API_ROOT, REPO_ROOT
from app.schemas.common import EvidenceStatus


def _find_yaml() -> Path:
    for base in (API_ROOT, REPO_ROOT):
        candidate = base / "corpus" / "matrix" / "remittance.yaml"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "corpus" / "matrix" / "remittance.yaml"


REMITTANCE_YAML = _find_yaml()


@dataclass(frozen=True, slots=True)
class LimitRule:
    """한도 한 건. `value` 는 status 가 unknown 이면 반드시 None 이다."""

    status: EvidenceStatus
    value: float | None
    evidence: tuple[dict, ...] = ()

    @property
    def known(self) -> bool:
        return self.status is not EvidenceStatus.UNKNOWN and self.value is not None


@dataclass(frozen=True, slots=True)
class Limits:
    annual_no_doc_usd: LimitRule
    per_transaction_no_doc_usd: LimitRule
    notes_block: dict


def _rule(raw: dict, where: str) -> LimitRule:
    status = EvidenceStatus(str(raw.get("status", "unknown")))
    value = raw.get("value")
    evidence = tuple(raw.get("evidence") or ())
    if status is not EvidenceStatus.UNKNOWN:
        # F2 의 `_require_evidence` 와 같은 규칙 — 근거 없이 official 인 셀은 없다.
        if not any(e.get("source_url") for e in evidence):
            raise SystemExit(
                f"{REMITTANCE_YAML}: {where} 의 status 가 {status.value} 인데 근거가 없습니다."
            )
        if value is None:
            raise SystemExit(f"{REMITTANCE_YAML}: {where} 의 status 가 {status.value} 인데 값이 없습니다.")
    elif value is not None:
        # unknown 인데 값이 있으면 화면 어딘가에서 새어 나간다.
        raise SystemExit(f"{REMITTANCE_YAML}: {where} 가 unknown 인데 값이 채워져 있습니다.")
    return LimitRule(status=status, value=None if value is None else float(value),
                     evidence=evidence)


@lru_cache(maxsize=1)
def load_limits() -> Limits:
    if not REMITTANCE_YAML.exists():
        raise SystemExit(f"송금 한도 카탈로그가 없습니다: {REMITTANCE_YAML}")
    raw = yaml.safe_load(REMITTANCE_YAML.read_text(encoding="utf-8"))
    lim = raw["limits"]
    return Limits(
        annual_no_doc_usd=_rule(lim["annual_no_doc_usd"], "annual_no_doc_usd"),
        per_transaction_no_doc_usd=_rule(
            lim["per_transaction_no_doc_usd"], "per_transaction_no_doc_usd"
        ),
        notes_block=raw,
    )


@dataclass(frozen=True, slots=True)
class Conversion:
    amount_krw: float
    rate_krw_per_usd: float
    amount_usd: float


def to_usd(amount_krw: float, rate_krw_per_usd: float) -> Conversion:
    """원화 금액을 매매기준율로 달러 환산한다.

    한도가 달러 기준이라 비교 전에 반드시 거치는 단계다. 반올림하지 않는다 —
    표시 자리수는 화면이 정하고, 계산은 원값을 유지한다.
    """
    if rate_krw_per_usd <= 0:
        raise ValueError("환율은 0보다 커야 합니다")
    return Conversion(
        amount_krw=amount_krw,
        rate_krw_per_usd=rate_krw_per_usd,
        amount_usd=amount_krw / rate_krw_per_usd,
    )


@dataclass(frozen=True, slots=True)
class LimitCheck:
    """한도 판정 하나.

    `status` 가 unknown 이면 `remaining`·`exceeds` 는 **모두 None** 이다 —
    "한도를 넘지 않았다"고 말하는 것도 근거 없는 주장이기 때문이다.
    """

    status: EvidenceStatus
    limit: float | None
    used: float | None
    remaining: float | None
    exceeds: bool | None


def check_annual(rule: LimitRule, self_declared_used_usd: float | None) -> LimitCheck:
    """연간 한도 판정.

    ★ `used` 는 **이용자가 직접 적은 값**이다. 우리는 ORIS 를 조회하지 않는다 —
      조회할 수단이 없고, 있는 것처럼 보이면 이용자가 이 숫자를 공식 잔액으로
      믿는다. 화면과 응답 양쪽에 자가 입력임을 명시한다(planner §10-F6 4번).
    """
    if not rule.known:
        return LimitCheck(status=rule.status, limit=None, used=self_declared_used_usd,
                          remaining=None, exceeds=None)
    limit = rule.value or 0.0
    used = self_declared_used_usd
    if used is None:
        return LimitCheck(status=rule.status, limit=limit, used=None,
                          remaining=None, exceeds=None)
    remaining = limit - used
    return LimitCheck(status=rule.status, limit=limit, used=used,
                      remaining=remaining, exceeds=remaining < 0)


def check_per_transaction(rule: LimitRule, amount_usd: float) -> LimitCheck:
    if not rule.known:
        return LimitCheck(status=rule.status, limit=None, used=amount_usd,
                          remaining=None, exceeds=None)
    limit = rule.value or 0.0
    return LimitCheck(status=rule.status, limit=limit, used=amount_usd,
                      remaining=limit - amount_usd, exceeds=amount_usd > limit)

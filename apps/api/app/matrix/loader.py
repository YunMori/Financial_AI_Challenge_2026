"""`corpus/matrix/institutions.yaml` 로더 (planner §4.2).

이 파일이 강제하는 것은 하나다 — **근거 없이 official 인 셀은 존재할 수 없다.**
yaml 머리말이 요구한 규칙이며, 여기서 막지 않으면 "공식 확인됨" 배지가 근거
링크 없이 화면에 뜬다. 그건 이 서비스가 가장 하지 말아야 할 일이다.

셀 상태는 3값이고(`EvidenceStatus`) **unknown 을 채우지 않는 것이 기능**이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import API_ROOT, REPO_ROOT
from app.schemas.common import EvidenceStatus

log = logging.getLogger(__name__)


def _find_matrix() -> Path:
    """매트릭스 위치.

    `app/rag/glossary.py::_find_glossary()` 와 같은 이유로 두 곳을 본다 —
    Docker 이미지는 `corpus/` 일부만 `/app` 아래에 복사하고 로컬은 리포 루트다.
    한쪽만 보면 **배포에서만** 기동이 깨진다(dev-log 2026-08-12).
    """
    for base in (API_ROOT, REPO_ROOT):
        candidate = base / "corpus" / "matrix" / "institutions.yaml"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "corpus" / "matrix" / "institutions.yaml"  # 에러 메시지용


MATRIX_YAML = _find_matrix()


@dataclass(frozen=True, slots=True)
class Evidence:
    """근거 한 건. `source_url` 이 없으면 근거로 치지 않는다."""

    doc_id: str
    source_url: str
    published_at: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class VisaRule:
    """체류자격 하나에 대한 기관의 계좌개설 요건."""

    visa_code: str
    account_open: EvidenceStatus
    channels: tuple[str, ...] = ()
    required_docs: tuple[str, ...] = ()
    purpose_docs: tuple[str, ...] = ()
    notes_ko: str = ""
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class Institution:
    """기관 카드 하나.

    ★ `dedicated_branch_count` 와 `languages_supported` 의 **None/빈 값은
    '없음'이 아니라 '미확인'** 이다. 이 구분을 접으면 확인하지 못한 것을
    "없다"고 주장하게 된다 — `fit.py` 가 이 값을 그렇게 다룬다.
    """

    inst_code: str
    inst_name_ko: str
    names_i18n: dict[str, str]
    status: EvidenceStatus
    channels: tuple[str, ...]
    mobile_arc_accepted: bool
    mobile_arc_since: str | None
    evidence: tuple[Evidence, ...]
    dedicated_branch_count: int | None
    languages_supported: tuple[str, ...]
    languages_status: EvidenceStatus
    visa_rules: dict[str, VisaRule]

    def name(self, lang: str) -> str:
        """대상 언어 표기. 없으면 한국어로 폴백한다 — 빈 카드보다 낫다."""
        if lang == "ko":
            return self.inst_name_ko
        return self.names_i18n.get(lang) or self.inst_name_ko

    def rule_for(self, visa: str | None) -> VisaRule | None:
        """해당 체류자격 규칙. **없으면 None 이고 그것이 unknown 을 뜻한다.**"""
        if not visa:
            return None
        return self.visa_rules.get(visa)

    @property
    def online_available(self) -> bool:
        return "online" in self.channels


@dataclass(frozen=True, slots=True)
class LimitedAccount:
    """한도제한계좌 — 기관별이 아니라 제도 차원의 사실 (F3·F4 가 함께 쓴다).

    ★ 거래유형별로 한도가 다르다. 기획서가 "1일 100만원"으로만 적은 부분이며
    창구는 300만원이다 (`spec-changes.md` #1).
    """

    status: EvidenceStatus
    electronic_transfer_krw: int
    atm_krw: int
    branch_krw: int
    effective_from: str
    notes_ko: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class Matrix:
    version: int
    updated_at: str
    institutions: tuple[Institution, ...]
    limited_account: LimitedAccount

    def get(self, inst_code: str) -> Institution | None:
        return next((i for i in self.institutions if i.inst_code == inst_code), None)


def _evidence(raw: list | None) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(
            doc_id=e["doc_id"],
            source_url=e["source_url"],
            published_at=e.get("published_at"),
            note=(e.get("note") or "").strip(),
        )
        for e in (raw or [])
    )


def _status(value: object, where: str) -> EvidenceStatus:
    try:
        return EvidenceStatus(value)
    except ValueError as exc:
        raise ValueError(f"{where}: 알 수 없는 셀 상태 {value!r}") from exc


def _require_evidence(status: EvidenceStatus, evidence: tuple[Evidence, ...], where: str) -> None:
    """★ 근거 없는 official 을 로드 시점에 막는다.

    화면까지 흘러가면 "공식 확인됨" 배지가 링크 없이 뜬다. 데이터 문제를
    데이터를 읽는 자리에서 끝낸다 — 뒤에서 방어하면 반드시 새는 곳이 생긴다.
    """
    if status is EvidenceStatus.OFFICIAL and not evidence:
        raise ValueError(f"{where}: status=official 인데 evidence 가 비어 있다")


def _parse_institution(raw: dict) -> Institution:
    code = raw["inst_code"]
    fc = raw["foreign_channel"]
    status = _status(fc["status"], code)
    evidence = _evidence(fc.get("evidence"))
    _require_evidence(status, evidence, f"{code}.foreign_channel")

    rules: dict[str, VisaRule] = {}
    for r in raw.get("visa_rules") or []:
        visa = r["visa_code"]
        where = f"{code}.{visa}"
        rule_status = _status(r["account_open"], where)
        rule_evidence = _evidence(r.get("evidence"))
        _require_evidence(rule_status, rule_evidence, where)
        rules[visa] = VisaRule(
            visa_code=visa,
            account_open=rule_status,
            channels=tuple(r.get("channels") or ()),
            required_docs=tuple(r.get("required_docs") or ()),
            purpose_docs=tuple(r.get("purpose_docs") or ()),
            notes_ko=(r.get("notes_ko") or "").strip(),
            evidence=rule_evidence,
        )

    return Institution(
        inst_code=code,
        inst_name_ko=raw["inst_name_ko"],
        names_i18n=dict(raw.get("inst_name_i18n") or {}),
        status=status,
        channels=tuple(fc.get("channels") or ()),
        mobile_arc_accepted=bool(fc.get("mobile_arc_accepted")),
        mobile_arc_since=fc.get("mobile_arc_since"),
        evidence=evidence,
        dedicated_branch_count=fc.get("dedicated_branch_count"),
        languages_supported=tuple(fc.get("languages_supported") or ()),
        languages_status=_status(fc.get("languages_status", "unknown"), f"{code}.languages"),
        visa_rules=rules,
    )


def _parse_limited_account(raw: dict) -> LimitedAccount:
    status = _status(raw["status"], "common.limited_account")
    evidence = _evidence(raw.get("evidence"))
    _require_evidence(status, evidence, "common.limited_account")
    limits = raw["daily_limits"]
    return LimitedAccount(
        status=status,
        electronic_transfer_krw=limits["electronic_transfer_krw"],
        atm_krw=limits["atm_krw"],
        branch_krw=limits["branch_krw"],
        effective_from=raw["effective_from"],
        notes_ko=(raw.get("notes_ko") or "").strip(),
        evidence=evidence,
    )


def load_matrix(path: Path | None = None) -> Matrix:
    yaml_path = path or MATRIX_YAML
    if not yaml_path.exists():
        raise SystemExit(f"요건 매트릭스가 없습니다: {yaml_path}")

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    matrix = Matrix(
        version=raw["version"],
        updated_at=str(raw["updated_at"]),
        institutions=tuple(_parse_institution(i) for i in raw["institutions"]),
        limited_account=_parse_limited_account(raw["common"]["limited_account"]),
    )
    log.info("요건 매트릭스 로드: 기관 %d · updated_at=%s",
             len(matrix.institutions), matrix.updated_at)
    return matrix


@lru_cache(maxsize=1)
def get_matrix() -> Matrix:
    """프로세스당 1회. yaml 은 배포 산출물이라 런타임에 바뀌지 않는다."""
    return load_matrix()

"""체크리스트 조립 (F3).

`corpus/matrix/documents.yaml` + `corpus/glossary/glossary.csv` → 절 3개.
**PDF 와 HTML 이 같은 함수를 쓴다** — 조판 미검증 언어에서 PDF 만 빼는
planner §12.3 대응이 두 경로의 내용을 갈라놓지 않으려면 조립이 하나여야 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import API_ROOT, REPO_ROOT
from app.i18n import checklist as strings
from app.matrix.service import resolve_docs
from app.schemas.checklist import ChecklistResponse, ChecklistSection
from app.schemas.common import EvidenceStatus, Lang, Tier
from app.schemas.institution import MatrixEvidence
from app.util.i18n_text import localized

log = logging.getLogger(__name__)


def _find_documents() -> Path:
    for base in (API_ROOT, REPO_ROOT):
        candidate = base / "corpus" / "matrix" / "documents.yaml"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "corpus" / "matrix" / "documents.yaml"


DOCUMENTS_YAML = _find_documents()


@dataclass(frozen=True, slots=True)
class DocumentCatalog:
    base: dict
    account_opening: dict
    foreign_registration: dict
    updated_at: str


@lru_cache(maxsize=1)
def load_catalog(path: Path | None = None) -> DocumentCatalog:
    yaml_path = path or DOCUMENTS_YAML
    if not yaml_path.exists():
        raise SystemExit(f"서류 카탈로그가 없습니다: {yaml_path}")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return DocumentCatalog(
        base=raw["base"],
        account_opening=raw["account_opening"],
        foreign_registration=raw["foreign_registration"],
        updated_at=str(raw["updated_at"]),
    )


_PUBLISHER_BY_PREFIX = {
    "FSC": "금융위원회",
    "KOREA": "대한민국 정책브리핑",
    "HIKOREA": "법무부 하이코리아",
    "KFB": "전국은행연합회",
}


def _evidence(items: list | None) -> list[MatrixEvidence]:
    return [
        MatrixEvidence(
            doc_id=e["doc_id"],
            publisher=_PUBLISHER_BY_PREFIX.get(e["doc_id"].split("-", 1)[0], "출처 미상"),
            published_at=e.get("published_at"),
            url=e["source_url"],
            note=(e.get("note") or "").strip(),
        )
        for e in (items or [])
    ]


def _status(value: str) -> EvidenceStatus:
    return EvidenceStatus(value)


# `_localized` 는 `app.util.i18n_text.localized` 로 옮겼다 — F8 도 같은 규칙을
# 읽으므로 사본을 두면 번역 누락 경고가 한쪽에만 남는다. 호출부를 바꾸지 않으려고
# 이름만 여기 묶어 둔다.
_localized = localized


def _section(key: str, block: dict, lang: Lang, caveat_block: dict | None = None) -> ChecklistSection:
    status = _status(block.get("status", "unknown"))
    evidence = _evidence(block.get("evidence"))
    if status is EvidenceStatus.OFFICIAL and not evidence:
        # 매트릭스 로더와 같은 불변식. 카탈로그도 예외가 아니다.
        raise ValueError(f"{key}: status=official 인데 evidence 가 비어 있다")
    return ChecklistSection(
        key=key,
        title=strings.section_title(key, lang),
        status=status,
        items=resolve_docs(block.get("doc_codes") or [], lang.value),
        notes=_localized(block, "notes", lang),
        caveat=_localized(caveat_block, "caveat", lang) if caveat_block else "",
        evidence=evidence,
    )


def _filename(visa: str | None, lang: Lang, today: date) -> str:
    """`KBuddy_checklist_E-9_vi_20260820.pdf` (planner §9.2)."""
    return f"KBuddy_checklist_{visa or 'ALL'}_{lang.value}_{today:%Y%m%d}.pdf"


def build_checklist(
    lang: Lang,
    visa: str | None = None,
    purpose: str | None = None,
    institution: str | None = None,
    today: date | None = None,
) -> ChecklistResponse:
    catalog = load_catalog()
    today = today or date.today()
    sections: list[ChecklistSection] = [_section("base", catalog.base, lang)]

    # ① 계좌개설 증빙 — 거래목적이 있을 때만. 원문이 "예시"임을 함께 싣는다.
    ao = catalog.account_opening
    if purpose:
        block = (ao.get("by_purpose") or {}).get(purpose)
        if block is None:
            # 카탈로그에 없는 목적은 조용히 빼지 않고 unknown 절로 남긴다.
            block = {
                "status": "unknown",
                "doc_codes": [],
                "notes_ko": strings.NOT_CONFIRMED[Lang.KO],
                "notes_i18n": {l.value: t for l, t in strings.NOT_CONFIRMED.items()},
                "evidence": [],
            }
        sections.append(_section("account_opening", block, lang, caveat_block=ao))

    # ② 외국인등록 — 계좌개설 요건이 **아니라는 것**이 이 절의 요점이다.
    fr = catalog.foreign_registration
    if visa:
        block = (fr.get("by_visa") or {}).get(visa)
        if block:
            sections.append(_section("foreign_registration", block, lang, caveat_block=fr))

    # 확인된 서류가 하나도 없으면 C — 체크리스트로서 성립하지 않는다.
    has_items = any(s.items for s in sections)
    tier = Tier.B if has_items else Tier.C

    log.info("checklist lang=%s visa=%s purpose=%s sections=%d tier=%s",
             lang.value, visa, purpose, len(sections), tier.value)

    return ChecklistResponse(
        lang=lang,
        visa=visa,
        purpose=purpose,
        institution=institution,
        sections=sections,
        disclaimer_tier=tier,
        generated_at=today.isoformat(),
        filename=_filename(visa, lang, today),
    )

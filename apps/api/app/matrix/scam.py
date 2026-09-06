"""사기 유형 대조 조립 (F8).

`corpus/matrix/scam.yaml` → `ScamResponse`. **LLM 이 없다** — F2 와 같이
경로 전체가 규칙이라 답이 매번 같고, 심사 시연에서 재현성이 보장된다.

★ **판정하지 않는다.** 이 모듈에는 "사기인가"를 계산하는 함수가 없고, 앞으로도
  두지 않는다. 개별 사안 판단은 계층 C 이며(planner §7.1), 데모 시나리오 3 의
  핵심 장면이 바로 그 거절이다. 대조표를 주는 것과 판정해 주는 것은 다르다.

★ **근거 없는 항목은 싣지 않는다.** 로더가 `evidence` 가 빈 항목에서 죽는다.
  사기 수법 목록은 "여기 없으면 안전하다"로 읽히기 쉬워서, 지어낸 항목 하나가
  목록 전체의 신뢰를 무너뜨린다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.config import API_ROOT, REPO_ROOT
from app.schemas.common import Lang
from app.schemas.institution import MatrixEvidence
from app.schemas.scam import ResponseStep, ScamContact, ScamResponse, ScamType
from app.util.i18n_text import localized


def _find_yaml() -> Path:
    """`matrix/loader.py::_find_matrix()` 와 같은 이유로 두 곳을 본다.

    Docker 이미지는 `corpus/` 의 일부만 `/app` 아래로 복사하고 로컬은 리포
    루트다. 한쪽만 보면 **배포에서만** 기동이 깨진다(dev-log 2026-08-12).
    """
    for base in (API_ROOT, REPO_ROOT):
        candidate = base / "corpus" / "matrix" / "scam.yaml"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "corpus" / "matrix" / "scam.yaml"  # 에러 메시지용


SCAM_YAML = _find_yaml()


def _evidence(raw: list | None, where: str) -> list[MatrixEvidence]:
    items = [
        MatrixEvidence(
            doc_id=e["doc_id"],
            publisher=e.get("publisher", ""),
            published_at=e.get("published_at"),
            url=e["source_url"],
            note=e.get("note", ""),
        )
        for e in (raw or [])
        if e.get("source_url")
    ]
    if not items:
        # 근거 없는 사기 수법은 그 자체가 환각이다 — 기동에서 막는다.
        raise SystemExit(f"{SCAM_YAML}: {where} 에 source_url 을 가진 근거가 없습니다.")
    return items


@lru_cache(maxsize=1)
def _raw() -> dict:
    if not SCAM_YAML.exists():
        raise SystemExit(f"사기 유형 카탈로그가 없습니다: {SCAM_YAML}")
    return yaml.safe_load(SCAM_YAML.read_text(encoding="utf-8"))


def build_response(lang: Lang) -> ScamResponse:
    raw = _raw()
    types = [
        ScamType(
            code=t["code"],
            certainty=t["certainty"],
            title=localized(t, "title", lang),
            body=localized(t, "body", lang),
            evidence=_evidence(t.get("evidence"), f"types[{t['code']}]"),
        )
        for t in raw["types"]
    ]
    steps = [
        ResponseStep(seq=s["seq"], label=localized(s, "label", lang))
        for s in sorted(raw["response_steps"], key=lambda s: s["seq"])
    ]
    contacts = [
        ScamContact(
            code=c["code"],
            number=c["number"],
            org=localized(c, "org", lang),
            role=localized(c, "role", lang),
            primary=bool(c.get("primary")),
            evidence=_evidence(c.get("evidence"), f"contacts[{c['code']}]"),
        )
        for c in raw["contacts"]
    ]
    return ScamResponse(
        lang=lang,
        types=types,
        response_steps=steps,
        response_evidence=_evidence(raw.get("response_evidence"), "response_evidence"),
        contacts=contacts,
        updated_at=str(raw["updated_at"]),
    )

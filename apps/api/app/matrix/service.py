"""매트릭스 → API 응답 조립 (F2).

로더가 낸 도메인 객체를 `schemas/institution.py` 로 옮기는 층이다.
**여기에도 LLM 은 없다** — 판정은 전부 규칙이라는 §6.1 의 성질이 F2 에서는
경로 전체로 확장된다.
"""

from __future__ import annotations

from app.matrix.fit import max_required_docs, score_institution
from app.matrix.loader import Evidence, Institution, get_matrix
from app.rag.glossary import load_glossary
from app.schemas.common import EvidenceStatus, Lang, Tier
from app.schemas.institution import (
    InstitutionCard,
    InstitutionsResponse,
    MatrixEvidence,
    RequiredDoc,
)

# 근거 문서의 발행 기관. doc_id 접두사로 가른다 — 문서 수가 적어 표로 충분하다.
_PUBLISHER_BY_PREFIX = {
    "FSC": "금융위원회",
    "KOREA": "대한민국 정책브리핑",
    "HIKOREA": "법무부 하이코리아",
    "KFB": "전국은행연합회",
}


def _publisher(doc_id: str) -> str:
    return _PUBLISHER_BY_PREFIX.get(doc_id.split("-", 1)[0], "출처 미상")


def _evidence(items: tuple[Evidence, ...]) -> list[MatrixEvidence]:
    return [
        MatrixEvidence(
            doc_id=e.doc_id,
            publisher=_publisher(e.doc_id),
            published_at=e.published_at,
            url=e.source_url,
            note=e.note,
        )
        for e in items
    ]


def resolve_docs(codes: tuple[str, ...] | list[str], lang: str) -> list[RequiredDoc]:
    """서류 코드 → 3언어 라벨.

    사전에 없는 코드는 **버리지 않고 코드 그대로 노출한다.** 조용히 사라지면
    체크리스트에서 서류 한 줄이 빠지고, 이용자는 창구에서 그걸 알게 된다.
    """
    glossary = load_glossary()
    docs: list[RequiredDoc] = []
    for code in codes:
        term = glossary.get(code)
        if term is None:
            docs.append(RequiredDoc(code=code, label=code, label_ko=code))
            continue
        docs.append(RequiredDoc(code=code, label=term.label(lang), label_ko=term.ko))
    return docs


def _card(inst: Institution, visa: str | None, lang: str, max_docs: int) -> InstitutionCard:
    rule = inst.rule_for(visa)
    fit = score_institution(inst, visa, max_docs)

    # 근거는 기관 단위 + 체류자격 단위를 합친다. doc_id 로 중복을 제거한다.
    merged: dict[str, Evidence] = {e.doc_id: e for e in inst.evidence}
    if rule:
        merged.update({e.doc_id: e for e in rule.evidence})

    return InstitutionCard(
        inst_code=inst.inst_code,
        inst_name=inst.name(lang),
        inst_name_ko=inst.inst_name_ko,
        status=inst.status,
        account_open=rule.account_open if rule else EvidenceStatus.UNKNOWN,
        # 체류자격별 채널이 확인됐으면 그것이 더 구체적이다. 없으면 기관 채널.
        channels=list(rule.channels) if rule and rule.channels else list(inst.channels),
        mobile_arc_accepted=inst.mobile_arc_accepted,
        mobile_arc_since=inst.mobile_arc_since,
        required_docs=resolve_docs(rule.required_docs if rule else (), lang),
        purpose_docs=resolve_docs(rule.purpose_docs if rule else (), lang),
        fit_score=fit.score,
        fit_reason=list(fit.reasons),
        unverified=list(fit.unverified),
        notes=rule.notes_ko if rule else "",
        evidence=_evidence(tuple(merged.values())),
    )


def build_response(lang: Lang, visa: str | None) -> InstitutionsResponse:
    matrix = get_matrix()
    max_docs = max_required_docs(matrix.institutions, visa)
    cards = [_card(i, visa, lang.value, max_docs) for i in matrix.institutions]

    # None(아는 것 없음)을 -1 로 눌러 맨 뒤로 보낸다. 0.0 과 자리를 바꾸지 않는다.
    cards.sort(key=lambda c: (-1.0 if c.fit_score is None else c.fit_score), reverse=True)

    unknown = [c.inst_code for c in cards if c.account_open is EvidenceStatus.UNKNOWN]

    # official 근거가 하나라도 있으면 B("일반적으로" + 사전확인 권고).
    # 계좌개설 요건 자체는 미확인이므로 A 는 나올 수 없다.
    has_official = any(c.status is EvidenceStatus.OFFICIAL for c in cards)
    tier = Tier.B if has_official else Tier.C

    return InstitutionsResponse(
        visa=visa,
        lang=lang,
        results=cards,
        unknown_institutions=unknown,
        disclaimer_tier=tier,
        updated_at=matrix.updated_at,
    )

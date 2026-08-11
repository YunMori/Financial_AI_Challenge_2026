"""⑥ 컨텍스트 조립 — 검색 결과를 프롬프트용 근거 블록으로 (planner §6.4).

인용 ID는 `[근거 n]` 형태로 부여한다. 긴 `chunk_id` 를 모델에게 출력시키면
오타가 나므로, 번호만 회수해 서버가 역매핑한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.config import get_settings
from app.rag.retrieve import Candidate, RetrievalResult
from app.schemas.common import EvidenceRef
from app.util.numerals import extract_numerals


@dataclass(slots=True)
class EvidenceContext:
    """생성에 넘길 근거 묶음. 출력 검사의 기준이기도 하다."""

    candidates: list[Candidate]
    block: str                          # 프롬프트에 들어갈 <context> 본문
    refs: list[EvidenceRef]             # 화면에 표시할 출처
    numerals: set[str] = field(default_factory=set)  # 숫자 대조용 정규형 집합
    has_stale: bool = False
    all_government: bool = True         # 전부 government 출처인가 (계층 A 조건)

    @property
    def n(self) -> int:
        # 근거 번호는 refs 에 매겨진다. candidates 는 비어 있을 수 있으므로
        # (검색 결과 없이 컨텍스트만 구성하는 테스트 등) refs 를 기준으로 센다.
        return len(self.refs)

    def ref_range(self) -> set[int]:
        """유효한 [근거 n] 번호. 인용 실재성 검사에 쓴다."""
        return set(range(1, self.n + 1))


def build_context(result: RetrievalResult, today: date | None = None) -> EvidenceContext:
    s = get_settings()
    today = today or date.today()

    lines: list[str] = []
    refs: list[EvidenceRef] = []
    numerals: set[str] = set()
    has_stale = False
    all_gov = True

    for i, c in enumerate(result.candidates, 1):
        stale = c.is_stale(today, s.stale_days)
        has_stale = has_stale or stale
        if c.meta.get("publisher_type") != "government":
            all_gov = False

        published = c.meta.get("published_at") or "미상"
        verified = c.meta.get("verified_at") or "미기재"
        header = (f"[근거 {i}] {c.meta['publisher']} 「{c.meta['title']}」 "
                  f"(발행 {published} / 확인 {verified}"
                  + (" / ⚠ 확인일이 오래됨" if stale else "") + ")")

        # 청크 텍스트의 첫 줄은 03_chunk 가 붙인 `[발행기관 / 제목 / 절]` 헤더다.
        # 여기서 다시 헤더를 만들므로 중복을 제거한다.
        body = c.text.split("\n", 1)[-1] if c.text.startswith("[") else c.text
        lines.append(f"{header}\n{body}")

        refs.append(EvidenceRef(
            ref=i,
            chunk_id=c.chunk_id,
            title=c.meta["title"],
            publisher=c.meta["publisher"],
            published_at=c.meta.get("published_at") or None,
            verified_at=c.meta.get("verified_at") or None,
            url=c.meta.get("source_url"),
            stale=stale,
        ))
        numerals |= extract_numerals(body)

    return EvidenceContext(
        candidates=result.candidates,
        block="\n\n".join(lines),
        refs=refs,
        numerals=numerals,
        has_stale=has_stale,
        all_government=all_gov and bool(result.candidates),
    )

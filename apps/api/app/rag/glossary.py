"""용어 사전 (planner §4.3).

두 방향으로 쓴다.

- **역매핑** (질의 정규화): `"Limited-Purpose Account"` → `"한도제한계좌"`.
  다국어 질의를 한국어 검색어로 바꿀 때 LLM 호출 없이 처리되는 경로다.
- **정매핑** (생성): 대상 언어의 고정 표기를 프롬프트에 주입하고, 출력에
  한국어 원어가 남았는지 검사한다.

**오역 시 치명적인 항목은 LLM 번역에 맡기지 않는다.** "재직증명서"가
"work certificate" 로 번역되면 이용자가 창구에서 다른 서류를 요구받는다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import API_ROOT, REPO_ROOT
from app.schemas.common import Lang


def _find_glossary() -> Path:
    """용어 사전 위치.

    Docker 이미지에는 `corpus/glossary/` 만 복사되므로 `API_ROOT`(=/app) 아래에
    있고, 로컬 개발에서는 리포 루트 아래에 있다. 둘 다 본다 —
    한쪽만 보면 배포에서만 깨지는 사고가 난다.
    """
    for base in (API_ROOT, REPO_ROOT):
        candidate = base / "corpus" / "glossary" / "glossary.csv"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "corpus" / "glossary" / "glossary.csv"  # 에러 메시지용


GLOSSARY_CSV = _find_glossary()

# 역매핑에서 무시할 짧은 표기. "ARC" 같은 코드가 일반 영단어와 충돌하는 것을 막는다.
MIN_LOOKUP_CHARS = 3


@dataclass(frozen=True, slots=True)
class Term:
    code: str
    ko: str
    category: str
    translations: dict[str, str]  # lang -> 표기 (빈 값은 제외)
    note: str = ""

    def label(self, lang: str) -> str:
        """대상 언어 표기. 없으면 한국어 원어를 병기해 폴백한다."""
        if lang == "ko":
            return self.ko
        if t := self.translations.get(lang):
            return t
        return f"{self.ko}"


class Glossary:
    def __init__(self, terms: list[Term]) -> None:
        self.terms = terms
        self._by_code = {t.code: t for t in terms}
        # 역매핑 색인: 소문자 표기 -> 한국어. 긴 표기를 먼저 매칭해야
        # "모바일 외국인등록증"이 "외국인등록증"으로 잘리지 않는다.
        index: dict[str, str] = {}
        for t in terms:
            for surface in (t.ko, *t.translations.values()):
                if surface and len(surface) >= MIN_LOOKUP_CHARS:
                    index.setdefault(surface.lower(), t.ko)
        self._surfaces = sorted(index, key=len, reverse=True)
        self._index = index

    def __len__(self) -> int:
        return len(self.terms)

    def get(self, code: str) -> Term | None:
        return self._by_code.get(code)

    def reverse_lookup(self, text: str) -> list[str]:
        """질의에 등장한 용어를 한국어 표기로 돌려준다 (등장 순서, 중복 제거).

        LLM 호출 없이 ~1ms 로 끝나는 경로다. 히트가 충분하면 정규화 단계에서
        LLM 을 건너뛴다(planner §6.2).
        """
        lowered = text.lower()
        found: dict[str, None] = {}
        consumed = lowered
        for surface in self._surfaces:  # 긴 것부터
            if surface in consumed:
                found.setdefault(self._index[surface], None)
                # 매칭된 구간을 지워 짧은 표기가 겹쳐 잡히지 않게 한다
                consumed = consumed.replace(surface, " ")
        return list(found)

    def for_prompt(self, lang: str, categories: tuple[str, ...] = ("document", "term", "visa")) -> str:
        """생성 프롬프트에 주입할 고정 용어표.

        시스템 프롬프트에 들어가며, sonnet-5 의 캐시 하한(1,024토큰)을
        넘기는 데도 기여한다.
        """
        lines = []
        for t in self.terms:
            if t.category not in categories:
                continue
            label = t.label(lang)
            lines.append(f"- {t.ko} → {label}" if label != t.ko else f"- {t.ko}")
        return "\n".join(lines)

    def korean_surfaces(self, categories: tuple[str, ...] = ("document", "term")) -> list[str]:
        """출력 검사용: 답변에 남아 있으면 안 되는 한국어 원어 목록."""
        return [t.ko for t in self.terms if t.category in categories]


@lru_cache(maxsize=1)
def load_glossary(path: Path | None = None) -> Glossary:
    csv_path = path or GLOSSARY_CSV
    if not csv_path.exists():
        raise SystemExit(f"용어 사전이 없습니다: {csv_path}")

    terms: list[Term] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            # ★ 언어 목록을 여기 박아 두면 안 된다. 예전에는 `("en", "vi")` 가
            #   하드코딩돼 있어서, CSV 에 zh·uz·th 열을 채워 넣어도 로더가
            #   읽지 않고 **조용히 한국어로 폴백**했다 (2026-08-21). 사전은
            #   오역이 치명적인 항목을 지키는 장치인데 그 장치가 꺼진 셈이었다.
            #   `Lang` 을 단일 출처로 삼아 언어를 늘릴 때 자동으로 따라오게 한다.
            translations = {
                lang.value: row[lang.value].strip()
                for lang in Lang
                if lang is not Lang.KO and row.get(lang.value, "").strip()
            }
            terms.append(Term(
                code=row["code"].strip(),
                ko=row["ko"].strip(),
                category=row["category"].strip(),
                translations=translations,
                note=row.get("note", "").strip(),
            ))
    return Glossary(terms)

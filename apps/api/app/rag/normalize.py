"""③ 질의 정규화 — 다국어 질의를 한국어 검색어로 (planner §6.2).

코퍼스는 한국어 정부 문서다. 베트남어 질의를 그대로 임베딩하면 다국어 모델이
어느 정도 흡수하지만, BM25 는 전혀 매칭되지 않아 하이브리드 검색의 절반이
죽는다. 그래서 검색 전에 한국어 검색어를 만든다.

**LLM 을 매번 부르지 않는다.** 3단 구조로 대부분을 사전에서 끝낸다:

1. 용어 사전 역매핑 (~1ms, LLM 없음)
2. 히트가 2개 이상이면 그것만으로 검색어를 구성하고 LLM 을 건너뛴다
3. 그 외에만 소형 모델(haiku) 1회 호출

세션 컨텍스트(체류자격)는 어느 경로든 검색어에 결합한다 — "E-9 급여수령
한도제한계좌 해제 서류" 처럼.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, Protocol

from app.rag.glossary import load_glossary
from app.rag.tokenize import extract_visa_codes

log = logging.getLogger(__name__)

# 사전 히트가 이만큼이면 LLM 없이 검색어를 만든다.
GLOSSARY_HIT_THRESHOLD = 2
CACHE_SIZE = 512


@dataclass(slots=True)
class NormalizedQuery:
    """검색에 쓰는 정규화 결과."""

    ko: str
    via: Literal["glossary", "llm", "passthrough"]
    visa: str | None = None
    glossary_hits: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.ko.strip()


class QueryTranslator(Protocol):
    """소형 LLM 어댑터. 없으면 passthrough 로 동작한다."""

    def to_search_terms(self, query: str, lang: str) -> str: ...


def _cache_key(query: str, lang: str, visa: str | None) -> str:
    raw = f"{lang}|{visa or ''}|{query}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@lru_cache(maxsize=CACHE_SIZE)
def _cached(key: str) -> NormalizedQuery | None:  # pragma: no cover - 캐시 골격
    return None


class QueryNormalizer:
    def __init__(self, translator: QueryTranslator | None = None) -> None:
        self._translator = translator
        self._glossary = load_glossary()
        self._cache: dict[str, NormalizedQuery] = {}

    def normalize(self, query: str, lang: str = "ko", visa: str | None = None) -> NormalizedQuery:
        key = _cache_key(query, lang, visa)
        if hit := self._cache.get(key):
            return hit
        result = self._normalize_uncached(query, lang, visa)
        # 데모 시나리오의 반복 질의는 100% 히트한다.
        if len(self._cache) >= CACHE_SIZE:
            self._cache.clear()
        self._cache[key] = result
        return result

    def _normalize_uncached(self, query: str, lang: str, visa: str | None) -> NormalizedQuery:
        # 질의에 체류자격이 있으면 세션 값보다 우선한다 — 이용자가 방금 말한
        # 쪽이 더 정확한 의도다("저는 E-9인데 D-2 친구는 어떤가요" 같은 경우).
        if codes := extract_visa_codes(query):
            visa = codes[0]

        hits = self._glossary.reverse_lookup(query)

        if lang == "ko":
            # 한국어는 원문이 이미 검색어다. 사전 히트는 기록만 해 둔다
            # (어떤 경로로 왔는지가 지표 산출에 쓰인다).
            return NormalizedQuery(
                ko=self._with_visa(query, visa), via="passthrough",
                visa=visa, glossary_hits=hits,
            )

        if len(hits) >= GLOSSARY_HIT_THRESHOLD:
            return NormalizedQuery(
                ko=self._with_visa(" ".join(hits), visa), via="glossary",
                visa=visa, glossary_hits=hits,
            )

        if self._translator is None:
            # 번역기가 없으면(API 키 미설정 등) 사전 히트라도 살려 쓴다.
            # 검색 품질은 떨어지지만 파이프라인이 멈추지는 않는다.
            log.debug("번역기 없음 — 사전 히트 %d건으로 진행", len(hits))
            fallback = " ".join(hits) if hits else query
            return NormalizedQuery(
                ko=self._with_visa(fallback, visa), via="passthrough",
                visa=visa, glossary_hits=hits,
            )

        try:
            ko = self._translator.to_search_terms(query, lang)
        except Exception as e:  # 번역 실패로 질의 자체를 잃지 않는다
            log.warning("질의 정규화 실패 (%s) — 사전 히트로 폴백", type(e).__name__)
            ko = " ".join(hits) if hits else query
            return NormalizedQuery(
                ko=self._with_visa(ko, visa), via="passthrough",
                visa=visa, glossary_hits=hits,
            )

        # 사전 히트를 검색어에 더한다. LLM 이 놓친 고정 용어를 보강하는 효과.
        merged = " ".join(dict.fromkeys([ko, *hits]))
        return NormalizedQuery(
            ko=self._with_visa(merged, visa), via="llm",
            visa=visa, glossary_hits=hits,
        )

    @staticmethod
    def _with_visa(text: str, visa: str | None) -> str:
        """체류자격을 검색어에 결합한다.

        BM25 가 E-9 와 E-7 을 다른 토큰으로 취급하므로, 코드가 검색어에
        들어가는 것만으로 변별력이 크게 올라간다.
        """
        if not visa:
            return text.strip()
        if visa.upper().replace("-", "") in text.upper().replace("-", ""):
            return text.strip()
        return f"{visa} {text}".strip()

"""④ 하이브리드 검색 — dense ∥ BM25 → RRF 융합 (planner §6.3).

두 검색기를 쓰는 이유는 서로의 실패를 메우기 때문이다.

- **dense** 는 의미가 비슷하면 표현이 달라도 찾는다. 대신 `E-9` 와 `E-7` 의
  벡터가 거의 같아 체류자격을 구분하지 못한다.
- **BM25** 는 `E-9` 와 `E-7` 을 완전히 다른 토큰으로 취급한다. 대신 표현이
  다르면 놓친다.

RRF 는 점수 척도가 다른 둘을 **순위만으로** 합친다. 정규화가 필요 없고
한쪽의 점수 분포가 바뀌어도 안정적이다.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.rag.embed import get_embedder
from app.rag.rerank import get_reranker, to_confidence
from app.rag.tokenize import tokenize

log = logging.getLogger(__name__)

# 메타데이터의 리스트는 구분자로 감싼 문자열로 저장돼 있다(04_index).
# 양끝 구분자가 오탐을 막는다: "|E-9|" 는 "|E-91|" 에 걸리지 않는다.
SEP = "|"


@dataclass(slots=True)
class Candidate:
    chunk_id: str
    text: str
    meta: dict[str, Any]
    dense_rank: int | None = None
    dense_score: float | None = None  # 코사인 유사도 (1 - distance)
    lexical_rank: int | None = None
    lexical_score: float | None = None
    rrf: float = 0.0
    rerank_score: float | None = None  # 0~1 (교차 인코더 로짓의 sigmoid)

    @property
    def visa_scope(self) -> list[str]:
        return [v for v in str(self.meta.get("visa_scope", "")).split(SEP) if v]

    def is_stale(self, today: date, stale_days: int) -> bool:
        """확인일이 오래된 문서인지. 확인일이 **비어 있으면 오래된 것으로 본다** —
        모르는 것을 최신으로 취급하면 시점 경고의 의미가 없다."""
        verified = str(self.meta.get("verified_at") or "")
        if not verified:
            return True
        try:
            return (today - date.fromisoformat(verified)).days > stale_days
        except ValueError:
            return True


@dataclass(slots=True)
class RetrievalResult:
    candidates: list[Candidate]
    top1_dense: float
    margin: float
    n_before_filter: int
    visa_filter_applied: bool
    visa_filter_relaxed: bool
    # 리랭킹을 했으면 1위의 리랭커 점수(0~1), 아니면 None.
    top1_rerank: float | None = None
    reranked: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.candidates

    @property
    def confidence(self) -> float:
        """게이트가 볼 점수.

        리랭킹을 했으면 리랭커 점수를, 아니면 dense top1 을 준다.
        **두 척도가 섞이면 안 되므로 판단 지점을 여기 하나로 모은다.**
        """
        if self.reranked and self.top1_rerank is not None:
            return self.top1_rerank
        return self.top1_dense


class HybridRetriever:
    def __init__(self, chroma_path: Path | None = None, bm25_path: Path | None = None) -> None:
        s = get_settings()
        self._chroma_path = chroma_path or s.chroma_path
        self._bm25_path = bm25_path or s.bm25_index_path
        self._collection = None
        self._bm25 = None
        self._chunk_ids: list[str] = []

    # ── 인덱스 로딩 (지연) ───────────────────────────────────────────

    @property
    def collection(self):
        if self._collection is None:
            import chromadb

            client = chromadb.PersistentClient(path=str(self._chroma_path))
            self._collection = client.get_collection(get_settings().chroma_collection)
        return self._collection

    def _load_bm25(self) -> None:
        if self._bm25 is not None:
            return
        payload = pickle.loads(self._bm25_path.read_bytes())
        self._bm25 = payload["bm25"]
        self._chunk_ids = payload["chunk_ids"]
        indexed = payload.get("embed_model")
        current = get_settings().embed_model
        if indexed and indexed != current:
            # 색인과 질의의 모델이 다르면 벡터 공간이 어긋나 검색이 조용히
            # 무너진다. 에러가 나지 않고 관련 없는 문서가 올라올 뿐이라
            # 반드시 경고해야 한다.
            log.warning(
                "인덱스는 %s 로 만들어졌는데 설정은 %s 입니다. "
                "04_index.py 를 다시 실행하세요.", indexed, current,
            )

    # ── 개별 검색기 ─────────────────────────────────────────────────

    def _dense(self, query: str, k: int) -> list[Candidate]:
        vector = get_embedder().embed_query(query)
        res = self.collection.query(
            query_embeddings=[vector],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        out: list[Candidate] = []
        for rank, (cid, doc, meta, dist) in enumerate(
            zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]), 1
        ):
            out.append(Candidate(
                chunk_id=cid, text=doc, meta=dict(meta),
                dense_rank=rank, dense_score=1.0 - float(dist),
            ))
        return out

    def _lexical(self, query: str, k: int) -> list[tuple[str, int, float]]:
        self._load_bm25()
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(zip(self._chunk_ids, scores), key=lambda x: -x[1])[:k]
        # 점수 0 은 매칭이 하나도 없다는 뜻이다. 순위를 주면 RRF 가
        # 무관한 문서를 끌어올린다.
        return [(cid, rank, float(s)) for rank, (cid, s) in enumerate(ranked, 1) if s > 0]

    # ── RRF 융합 ────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        visa: str | None = None,
        top_k: int | None = None,
        top_n: int | None = None,
    ) -> RetrievalResult:
        s = get_settings()
        top_k = top_k or s.retrieve_top_k
        top_n = top_n or s.context_top_n

        dense = self._dense(query, top_k)
        lexical = self._lexical(query, top_k)

        by_id: dict[str, Candidate] = {c.chunk_id: c for c in dense}
        for cid, rank, score in lexical:
            cand = by_id.get(cid)
            if cand is None:
                # BM25 에만 있는 후보는 본문을 따로 가져온다.
                fetched = self.collection.get(ids=[cid], include=["documents", "metadatas"])
                if not fetched["ids"]:
                    continue
                cand = Candidate(chunk_id=cid, text=fetched["documents"][0],
                                 meta=dict(fetched["metadatas"][0]))
                by_id[cid] = cand
            cand.lexical_rank, cand.lexical_score = rank, score

        for cand in by_id.values():
            score = 0.0
            if cand.dense_rank:
                score += 1.0 / (s.rrf_k + cand.dense_rank)
            if cand.lexical_rank:
                score += s.rrf_w_lexical / (s.rrf_k + cand.lexical_rank)
            cand.rrf = score

        fused = sorted(by_id.values(), key=lambda c: -c.rrf)
        n_before = len(fused)

        # 체류자격 필터. 후보가 너무 적어지면 해제한다(과필터 방지) —
        # 근거를 못 찾아 폴백하는 것보다 넓게 찾고 계층 판정에 맡기는 편이 낫다.
        relaxed = False
        applied = False
        if visa:
            applied = True
            keep = [c for c in fused if not c.visa_scope
                    or visa in c.visa_scope or "ALL" in c.visa_scope]
            if len(keep) >= 3:
                fused = keep
            else:
                relaxed = True
                log.debug("체류자격 필터 해제 (후보 %d건)", len(keep))

        # 변별력: 1위와 3위의 RRF 점수 차. 상위가 평평하면 무엇을 골라도
        # 비슷하다는 뜻이고, 그것은 근거가 약하다는 신호다.
        # **리랭킹 전에 잰다** — 리랭커가 순서를 바꾼 뒤에 재면 RRF 차이가
        # 아닌 값이 margin 이라는 이름으로 보고된다.
        margin = (fused[0].rrf - fused[2].rrf) if len(fused) >= 3 else 0.0

        # ── ⑤ 리랭킹 ────────────────────────────────────────────────
        # RRF 상위 N 건을 교차 인코더로 다시 매긴다. RRF 는 순위만 합치므로
        # "질의에 실제로 답이 되는가"를 보지 못한다 — 그걸 여기서 본다.
        reranked = False
        top1_rerank: float | None = None
        pool = fused[:max(top_n, s.rerank_top_n)]
        reranker = get_reranker()
        if reranker.enabled and pool:
            logits = reranker.score(query, [c.text for c in pool])
            if len(logits) == len(pool):
                for cand, logit in zip(pool, logits):
                    cand.rerank_score = to_confidence(logit)
                pool = sorted(pool, key=lambda c: -(c.rerank_score or 0.0))
                fused = pool + fused[len(pool):]
                reranked = True
            else:  # pragma: no cover - 백엔드가 개수를 어기면 순위를 믿을 수 없다
                log.warning("리랭커가 %d건 후보에 %d개 점수를 반환 — 리랭킹을 건너뜁니다",
                            len(pool), len(logits))

        selected = fused[:top_n]
        top1_dense = max((c.dense_score or 0.0 for c in selected), default=0.0)
        if reranked and selected:
            top1_rerank = selected[0].rerank_score

        return RetrievalResult(
            candidates=selected,
            top1_dense=top1_dense,
            margin=margin,
            n_before_filter=n_before,
            visa_filter_applied=applied,
            visa_filter_relaxed=relaxed,
            top1_rerank=top1_rerank,
            reranked=reranked,
        )


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    return HybridRetriever()

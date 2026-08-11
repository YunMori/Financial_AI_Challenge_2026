"""④ 색인 — 청크를 임베딩해 Chroma 에 넣고 BM25 인덱스를 만든다.

**문서 임베딩은 빌드 타임에 끝낸다.** 산출물(`apps/api/data/`)을 Docker
이미지에 동봉하면 런타임은 쿼리 1건만 임베딩하면 되고, 콜드스타트가
크게 줄어든다(planner §15.3 — 런타임 다운로드 금지).

BM25 는 `app.rag.tokenize` 를 그대로 쓴다. 색인과 질의가 같은 토크나이저를
통과해야 하며, 한쪽만 사용자 사전을 쓰면 토큰이 어긋나 BM25 가 조용히
무력화된다.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

from _common import CHUNKS_JSONL, REPO_ROOT

# 런타임과 같은 토크나이저·임베더를 쓰기 위해 API 앱을 경로에 넣는다.
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.rag.embed import get_embedder  # noqa: E402
from app.rag.tokenize import tokenize  # noqa: E402

DATA_DIR = REPO_ROOT / "apps" / "api" / "data"
CHROMA_PATH = DATA_DIR / "chroma"
BM25_PATH = DATA_DIR / "bm25.pkl"
COLLECTION = "kbuddy"

# Chroma 메타데이터는 스칼라만 받는다. 리스트는 구분자로 감싼 문자열로 넣고,
# 검색 시 `$contains` 로 부분일치시킨다(양끝 구분자가 오탐을 막는다:
# "|E-9|" 는 "|E-91|" 에 걸리지 않는다).
SEP = "|"


def pack_list(values: list[str]) -> str:
    return SEP + SEP.join(values) + SEP if values else SEP


def build_metadata(row: dict) -> dict:
    return {
        "doc_id": row["doc_id"],
        "seq": row["seq"],
        "title": row["title"],
        "publisher": row["publisher"],
        "publisher_type": row["publisher_type"],
        "doc_type": row["doc_type"],
        "published_at": row.get("published_at") or "",
        "verified_at": row.get("verified_at") or "",
        "source_url": row["source_url"],
        "topics": pack_list(row.get("topics") or []),
        "visa_scope": pack_list(row.get("visa_scope") or ["ALL"]),
        "is_table": bool(row.get("is_table")),
        "char_count": row["char_count"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true",
                    help="기존 컬렉션을 지우고 새로 만든다")
    args = ap.parse_args()

    if not CHUNKS_JSONL.exists():
        print("chunks.jsonl 이 없습니다. 03_chunk.py 를 먼저 실행하세요.")
        return 1

    rows = [json.loads(line) for line in CHUNKS_JSONL.read_text(encoding="utf-8").splitlines() if line]
    print(f"청크 {len(rows)}개 로드")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── 임베딩 ───────────────────────────────────────────────────────
    t0 = time.perf_counter()
    embedder = get_embedder()
    print(f"임베더: {embedder.model_name} (dim={embedder.dim}) "
          f"로드 {time.perf_counter() - t0:.1f}s")

    t0 = time.perf_counter()
    vectors = embedder.embed_passages([r["text"] for r in rows])
    print(f"임베딩 {len(vectors)}건 {time.perf_counter() - t0:.1f}s")

    # ── Chroma ───────────────────────────────────────────────────────
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    if args.rebuild:
        try:
            client.delete_collection(COLLECTION)
            print("기존 컬렉션 삭제")
        except Exception:
            pass

    # 임베딩을 직접 넣으므로 Chroma 의 기본 임베딩 함수를 쓰지 않는다.
    collection = client.get_or_create_collection(
        COLLECTION,
        metadata={"hnsw:space": "cosine", "embed_model": embedder.model_name},
        embedding_function=None,
    )
    collection.upsert(
        ids=[r["chunk_id"] for r in rows],
        embeddings=vectors,
        documents=[r["text"] for r in rows],
        metadatas=[build_metadata(r) for r in rows],
    )
    print(f"Chroma 저장 {collection.count()}건 → {CHROMA_PATH.relative_to(REPO_ROOT)}")

    # ── BM25 ─────────────────────────────────────────────────────────
    from rank_bm25 import BM25Okapi

    t0 = time.perf_counter()
    corpus_tokens = [tokenize(r["text"]) for r in rows]
    empty = [r["chunk_id"] for r, t in zip(rows, corpus_tokens) if not t]
    bm25 = BM25Okapi(corpus_tokens)
    BM25_PATH.write_bytes(pickle.dumps({
        "bm25": bm25,
        "chunk_ids": [r["chunk_id"] for r in rows],
        "embed_model": embedder.model_name,
    }))
    avg = sum(len(t) for t in corpus_tokens) / len(corpus_tokens)
    print(f"BM25 저장 {len(corpus_tokens)}건 (평균 {avg:.0f}토큰) "
          f"{time.perf_counter() - t0:.1f}s → {BM25_PATH.relative_to(REPO_ROOT)}")

    if empty:
        print(f"\n⚠ 토큰이 하나도 없는 청크 {len(empty)}건: {', '.join(empty[:5])}")
        print("  BM25 에서 절대 검색되지 않습니다. 정제 결과를 확인하세요.")

    print(f"\n산출물은 Docker 이미지에 동봉됩니다 "
          f"({DATA_DIR.relative_to(REPO_ROOT)}/ — .gitignore 대상)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

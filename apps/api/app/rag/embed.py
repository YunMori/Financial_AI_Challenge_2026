"""임베딩 백엔드 추상화.

**색인과 질의가 반드시 같은 모델·같은 접두어 규칙을 써야 한다.** 한쪽만
바꾸면 벡터 공간이 어긋나 검색이 조용히 무너진다 — 에러가 나지 않고 그냥
관련 없는 문서가 상위에 올라온다.

백엔드가 둘인 이유
------------------
계획은 BGE-m3 를 상정했으나 **fastembed 는 BGE-m3 를 지원하지 않는다**
(2026-08 기준 다국어 옵션: multilingual-e5-large / mpnet / MiniLM).
BGE-m3·KURE 를 쓰려면 sentence-transformers(torch) 가 필요하고, 이는
배포 메모리 예산에 직접 영향을 준다(ADR-003 은 Render 전제로 쓰였고 AWS 기준 재작성 대기).

그래서 지금 결정하지 않고 **교체 가능하게** 만들어 두고, Phase 7 의 컨테이너
메모리 실측 후 ADR-003 으로 확정한다. `EMBED_BACKEND` 환경변수로 바꾼다.

- `fastembed` (기본): ONNX, torch 불필요, 메모리 작음
- `sentence_transformers`: BGE-m3 / KURE 등 임의 모델. torch 필요(무거움)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol, Sequence

# e5 계열은 접두어가 **필수**다. 붙이지 않으면 성능이 크게 떨어지는데,
# 에러가 나지 않아 알아채기 어렵다. BGE 계열은 접두어를 쓰지 않는다.
E5_QUERY_PREFIX = "query: "
E5_PASSAGE_PREFIX = "passage: "


class Embedder(Protocol):
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _needs_e5_prefix(model_name: str) -> bool:
    return "e5" in model_name.lower()


class FastEmbedBackend:
    """ONNX 기반. torch 를 끌어오지 않아 이미지가 가볍다."""

    def __init__(self, model_name: str) -> None:
        # ★ onnxruntime-gpu 는 nvidia-* pip 패키지의 CUDA DLL 을 Windows 검색
        #   경로에 자동 등록하지 않는다(`site-packages/nvidia/cu13/bin/x86_64/`).
        #   이걸 부르지 않으면 CUDAExecutionProvider 생성이 실패하고 **에러 없이
        #   CPU 로 떨어진다** — e5-large 32건 기준 88ms → 2,857ms (32배).
        #
        #   ⚠ `ort.get_available_providers()` 는 이 상태에서도 CUDAExecutionProvider
        #     를 보고한다. providers 문자열만 보고 판단하면 안 된다 — 실제로 세션을
        #     만들어 `session.get_providers()` 로 확인해야 한다 (dev-log 2026-08-19).
        #
        #   CPU 전용 onnxruntime(Docker)에는 없거나 무의미하므로 실패를 삼킨다.
        try:
            import onnxruntime

            onnxruntime.preload_dlls()
        except Exception:  # pragma: no cover - 장치·빌드에 따라 갈린다
            pass

        from fastembed import TextEmbedding

        # providers 를 넘기지 않는다. fastembed 0.8 의 `cuda=Device.AUTO` 기본값이
        # CUDA EP 가 살아 있으면 잡고 없으면 CPU 로 간다 — 장치별 분기가 필요 없다.
        self._model = TextEmbedding(model_name=model_name)
        self._prefix = _needs_e5_prefix(model_name)
        self.model_name = model_name
        self.dim = self._probe_dim()

    def _probe_dim(self) -> int:
        return len(next(iter(self._model.embed(["차원 확인"]))))

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        if self._prefix:
            texts = [E5_PASSAGE_PREFIX + t for t in texts]
        return [list(map(float, v)) for v in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        if self._prefix:
            text = E5_QUERY_PREFIX + text
        return list(map(float, next(iter(self._model.query_embed([text])))))


class SentenceTransformersBackend:
    """임의 HuggingFace 모델(BGE-m3, KURE 등). torch 가 필요하다."""

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover - 설치 안내가 목적
            raise SystemExit(
                f"{model_name} 를 쓰려면 sentence-transformers 가 필요합니다.\n"
                "  pip install sentence-transformers\n"
                "  (torch 를 함께 설치하므로 이미지가 크게 늘어납니다 — "
                "Phase 7 메모리 실측 전에는 fastembed 백엔드를 권장)"
            ) from e

        self._model = SentenceTransformer(model_name)
        self._prefix = _needs_e5_prefix(model_name)
        self.model_name = model_name
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        if self._prefix:
            texts = [E5_PASSAGE_PREFIX + t for t in texts]
        return self._model.encode(list(texts), normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        if self._prefix:
            text = E5_QUERY_PREFIX + text
        return self._model.encode([text], normalize_embeddings=True)[0].tolist()


@lru_cache(maxsize=1)
def get_embedder(model_name: str | None = None, backend: str | None = None) -> Embedder:
    """프로세스당 한 번만 모델을 올린다.

    런타임에서는 쿼리 1건만 임베딩한다. 문서 임베딩은 빌드 타임(04_index)에
    끝내 Chroma 에 넣고 이미지에 동봉하므로, 서버는 추론만 한다.

    모델명의 **단일 출처는 `app.config.Settings`** 다. 여기서 따로 기본값을
    두면 색인기와 런타임이 다른 모델을 쓰는 사고가 난다(실제로 겪음 —
    dev-log 2026-08-12 참조). 벡터 공간이 어긋나면 에러 없이 검색만 무너진다.
    """
    from app.config import get_settings

    settings = get_settings()
    model_name = model_name or settings.embed_model
    backend = backend or os.getenv("EMBED_BACKEND", "fastembed")
    if backend == "sentence_transformers":
        return SentenceTransformersBackend(model_name)
    if backend == "fastembed":
        return FastEmbedBackend(model_name)
    raise SystemExit(f"알 수 없는 EMBED_BACKEND: {backend!r} (fastembed | sentence_transformers)")

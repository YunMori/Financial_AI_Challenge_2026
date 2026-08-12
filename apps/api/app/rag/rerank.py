"""⑤ 리랭킹 — 교차 인코더 (planner §6.5, ADR-001).

왜 넣는가
---------
dense 코사인 유사도는 **유사도**이지 **답할 수 있는가**가 아니다. e5 의 점수는
0.75~0.91 의 좁은 띠에 몰려 있고, 골든셋 100문항으로 재 보면 코퍼스 안 질의의
최솟값이 밖 질의의 최댓값보다 **낮다**(ko −0.032 / en −0.023 / vi −0.011).
분리 폭이 음수라는 것은 **어떤 임계값을 골라도 두 목표를 동시에 만족할 수
없다**는 뜻이다 — 임계값의 위치 문제가 아니라 신호의 문제다.

교차 인코더는 질의와 문서를 **함께** 보고 관련성을 직접 채점하므로 점수의
동적 범위가 넓다. 그래서 순위(Recall@5)와 신뢰도 게이트를 동시에 개선한다.

Recall@20 이 100% 라는 실측이 이 선택의 근거다. 정답 문서는 이미 전부
후보에 들어와 있고, 상위 5건 밖으로 밀려 있을 뿐이었다.

교체 가능하게 두는 이유
----------------------
`embed.py` 와 같다. 모델 선택이 메모리 예산과 직결되므로 환경변수로 바꿀 수
있어야 하고, 끄는 것(`RERANK_BACKEND=none`)도 하나의 선택지여야 한다 —
리랭킹 없는 상태가 회귀 비교의 기준선이기 때문이다.
"""

from __future__ import annotations

import logging
import math
import os
from functools import lru_cache
from typing import Protocol, Sequence

log = logging.getLogger(__name__)


class Reranker(Protocol):
    enabled: bool

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """관련성 점수(로짓). 클수록 관련 있음."""
        ...


def to_confidence(logit: float) -> float:
    """로짓 → 0~1 신뢰도.

    교차 인코더는 부호 있는 로짓을 낸다. 그대로 임계값을 잡아도 동작하지만,
    0~1 로 옮겨야 dense 점수와 같은 척도로 읽히고 설정값이 해석 가능해진다.
    **단조 변환이므로 순위와 분리 폭의 성질은 그대로다.**
    """
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    e = math.exp(logit)  # logit 이 크게 음수일 때 exp 오버플로 방지
    return e / (1.0 + e)


class NullReranker:
    """리랭킹을 하지 않는다. 기준선이자 비상 스위치."""

    enabled = False

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return []


class FastEmbedReranker:
    """ONNX 교차 인코더. torch 를 끌어오지 않는다.

    모델을 **지연 로드**한다. 임포트 시점에 1GB 를 올리면 테스트와 CLI 가
    전부 느려지고, 리랭킹을 끈 설정에서도 메모리를 먹는다.
    """

    enabled = True

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._encoder = None

    @property
    def encoder(self):
        if self._encoder is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            log.info("리랭커 로드: %s", self.model_name)
            self._encoder = TextCrossEncoder(model_name=self.model_name)
        return self._encoder

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self.encoder.rerank(query, list(texts))]


@lru_cache(maxsize=1)
def get_reranker(model_name: str | None = None, backend: str | None = None) -> Reranker:
    """프로세스당 한 번만 올린다.

    모델명의 단일 출처는 `app.config.Settings` 다 — `embed.py` 와 같은 이유로,
    여기에 기본값을 따로 두면 설정과 실제가 어긋나도 아무도 모른다.
    """
    from app.config import get_settings

    s = get_settings()
    backend = backend or os.getenv("RERANK_BACKEND", "fastembed" if s.rerank_enabled else "none")
    if backend == "none":
        return NullReranker()
    if backend == "fastembed":
        return FastEmbedReranker(model_name or s.rerank_model)
    raise SystemExit(f"알 수 없는 RERANK_BACKEND: {backend!r} (fastembed | none)")

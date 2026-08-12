"""⑤ 리랭킹 (ADR-001).

여기서 지키려는 것은 리랭커의 품질이 아니라 **게이트가 조용히 꺼지지 않는
것**이다. 이 프로젝트에서 반복해 나온 실패 모드가 정확히 그것이다 —
에러 없이 안전장치만 사라지는 경우(임계값 0.42, 전체 STALE, 캐시 미적중).
"""

from __future__ import annotations

import math

import pytest

from app.config import get_settings
from app.rag.rerank import FastEmbedReranker, NullReranker, get_reranker, to_confidence
from app.schemas.common import Lang


class TestToConfidence:
    def test_zero_logit_is_half(self):
        assert to_confidence(0.0) == pytest.approx(0.5)

    def test_monotonic(self):
        """단조 변환이어야 순위와 분리 폭의 성질이 보존된다."""
        xs = [-8.0, -3.0, -0.5, 0.0, 0.5, 3.0, 8.0]
        vs = [to_confidence(x) for x in xs]
        assert vs == sorted(vs)

    def test_bounded(self):
        for x in (-500.0, -20.0, 0.0, 20.0, 500.0):
            assert 0.0 <= to_confidence(x) <= 1.0

    def test_no_overflow_on_large_negative(self):
        """`1/(1+exp(-x))` 를 그대로 쓰면 x 가 크게 음수일 때 exp 가 넘친다."""
        assert to_confidence(-1000.0) == pytest.approx(0.0, abs=1e-12)
        assert to_confidence(1000.0) == pytest.approx(1.0, abs=1e-12)
        assert not math.isnan(to_confidence(-1000.0))


class TestBackendSelection:
    def test_none_backend_is_disabled(self):
        get_reranker.cache_clear()
        r = get_reranker(backend="none")
        assert isinstance(r, NullReranker)
        assert r.enabled is False
        assert r.score("질의", ["문서"]) == []
        get_reranker.cache_clear()

    def test_fastembed_backend_does_not_load_model_eagerly(self):
        """1GB 모델을 임포트 시점에 올리면 테스트와 CLI 가 전부 느려진다."""
        r = FastEmbedReranker("BAAI/bge-reranker-base")
        assert r.enabled is True
        assert r._encoder is None

    def test_unknown_backend_fails_loudly(self):
        get_reranker.cache_clear()
        with pytest.raises(SystemExit):
            get_reranker(backend="설정오타")
        get_reranker.cache_clear()

    def test_empty_texts_short_circuits(self):
        """후보가 없으면 모델을 건드리지 않는다."""
        r = FastEmbedReranker("BAAI/bge-reranker-base")
        assert r.score("질의", []) == []
        assert r._encoder is None


class TestGateIsArmed:
    """★ 게이트가 꺼진 채 배포되는 것을 막는다.

    임계값이 0 이면 `confidence < threshold` 가 **영원히 거짓**이 되어
    폴백이 한 번도 걸리지 않는다. 에러도 로그도 나지 않는다.
    "근거가 없으면 답하지 않는다"는 주장이 통째로 사라지는데도 조용하다.
    """

    def test_active_threshold_is_never_zero(self):
        s = get_settings()
        for lang in Lang:
            assert s.threshold_for(lang.value) > 0.0, (
                f"{lang.value} 의 실효 임계값이 0 입니다 — 폴백이 전혀 걸리지 않습니다. "
                "리랭킹을 켰다면 `python -m app.rag.cli --calibrate` 로 "
                "threshold_rerank_by_lang 을 채우세요."
            )

    def test_every_public_language_has_a_rerank_threshold(self):
        s = get_settings()
        if not s.rerank_enabled:
            pytest.skip("리랭킹 비활성")
        for lang in Lang:
            assert lang.value in s.threshold_rerank_by_lang, (
                f"{lang.value} 리랭커 임계값이 없습니다. 공개 언어를 늘렸다면 재보정하세요."
            )

    def test_threshold_scale_follows_rerank_flag(self):
        """두 척도를 섞으면 게이트가 무너진다 — 판단 지점은 한 곳뿐이어야 한다."""
        s = get_settings()
        expected = (s.threshold_rerank_by_lang if s.rerank_enabled
                    else s.threshold_top1_by_lang)
        assert s.threshold_for("ko") == expected.get("ko", 0.0) or not expected

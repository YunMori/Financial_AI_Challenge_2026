"""로컬 생성 백엔드 (ADR-004).

여기서 지키려는 것은 모델 품질이 아니라 **배선**이다. 이 프로젝트에서
반복해 나온 실패가 "설정은 A 인데 실행은 B" 였다 — 색인기와 런타임이 다른
임베딩 모델을 볼 뻔했고, 임계값이 세 파일로 갈라져 배포에 나가던 값이
문서와 달랐고, 키가 있는데도 ③ 번역이 꺼진 채 채점됐다.

**모델을 내려받지 않는다.** 순수 함수와 지연 로드 성질만 고정한다.
"""

from __future__ import annotations

import pytest

from app.llm.base import NullLLMClient
from app.llm.local_client import LocalLLMClient, resolve_device, resolve_dtype


class TestDeviceResolution:
    def test_explicit_preference_wins(self):
        """강제 지정은 자동 판정을 이긴다 — 비교 실험에서 장치를 고정해야 한다."""
        assert resolve_device("cpu") == "cpu"
        assert resolve_device("cuda") == "cuda"

    def test_auto_returns_a_real_device(self):
        assert resolve_device("auto") in ("cuda", "mps", "cpu")

    @pytest.mark.parametrize(
        "device,expected",
        [("cuda", "bfloat16"), ("mps", "float16"), ("cpu", "float32")],
    )
    def test_dtype_defaults_per_device(self, device, expected):
        """mps 는 bfloat16 지원이 고르지 않아 float16, cpu 는 float16 이 오히려 느리다."""
        import torch

        assert resolve_dtype(device, "auto") is getattr(torch, expected)

    def test_explicit_dtype_wins(self):
        import torch

        assert resolve_dtype("mps", "float32") is torch.float32


class TestLazyLoad:
    """수 GB 를 임포트 시점에 올리면 이 백엔드를 쓰지 않는 설정에서도 메모리를 먹는다."""

    def test_construction_does_not_load_model(self):
        c = LocalLLMClient(model_name="google/gemma-3-4b-it", device="cpu")
        assert c._model is None
        assert c._tokenizer is None
        assert c._compiler is None

    def test_model_name_comes_from_settings_by_default(self):
        from app.config import get_settings

        assert LocalLLMClient().model_name == get_settings().local_model


class TestBackendSelection:
    """★ 로컬 경로가 API 키를 요구하면 '완전 로컬'이라는 주장이 성립하지 않는다."""

    def test_local_backend_ignores_missing_api_key(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("LLM_BACKEND", "local")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        get_settings.cache_clear()
        try:
            from app.llm.anthropic_client import build_client

            client = build_client()
            assert isinstance(client, LocalLLMClient)
            # NullLLMClient 가 아니어야 파이프라인이 ③ 번역기를 배선한다.
            assert not isinstance(client, NullLLMClient)
        finally:
            get_settings.cache_clear()

    def test_anthropic_backend_without_key_is_null(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("LLM_BACKEND", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        get_settings.cache_clear()
        try:
            from app.llm.anthropic_client import build_client

            assert isinstance(build_client(), NullLLMClient)
        finally:
            get_settings.cache_clear()


class TestUnimplementedPathsFailLoudly:
    """Step 1·2 이전에는 조용히 빈 답을 내지 말고 **정직하게 실패**해야 한다."""

    @pytest.mark.asyncio
    async def test_translate_raises(self):
        from app.llm.base import GenerationFailed

        with pytest.raises(GenerationFailed):
            await LocalLLMClient(device="cpu").translate_to_search_terms("hi", "en")

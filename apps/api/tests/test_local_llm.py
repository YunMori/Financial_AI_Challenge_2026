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


class TestFailsLoudly:
    """조용히 빈 답을 내지 말고 **정직하게 실패**해야 한다.

    폴백은 이용자에게 "근거를 찾지 못했다"로 보이지만, 빈 문자열이 검색어로
    들어가면 엉뚱한 문서가 상위에 오고 그 위에서 답이 생성된다.
    """

    @pytest.mark.asyncio
    async def test_generation_not_implemented_yet(self):
        """Step 2 이전. 모델을 올리지 않고도 확인된다."""
        from app.llm.base import GenerationFailed
        from app.schemas.common import Lang

        with pytest.raises(GenerationFailed):
            async for _ in LocalLLMClient(device="cpu").stream(
                system="s", user="u", lang=Lang.KO,
            ):
                pass

    @pytest.mark.asyncio
    async def test_translate_wraps_load_failure(self, monkeypatch):
        """모델 로드가 실패하면 프로토콜 예외로 좁혀 올린다.

        ★ 모델을 실제로 내려받지 않는다 — 로드를 가짜로 실패시켜 **경로만** 본다.
        예전 이 테스트는 "구현 안 됨"을 보고 있었는데, 구현 후에는 모델이
        없어서 통과하는 상태가 됐다. 통과 이유가 바뀐 테스트는 테스트가 아니다.
        """
        from app.llm.base import GenerationFailed

        client = LocalLLMClient(device="cpu")

        def boom() -> None:
            raise RuntimeError("가짜 로드 실패")

        monkeypatch.setattr(client, "_load", boom)
        with pytest.raises(GenerationFailed):
            await client.translate_to_search_terms("hi", "en")


class TestSearchTermCleaning:
    """모델 출력에서 검색어만 남긴다.

    "설명 없이 검색어만"이라고 지시해도 소형 모델은 서두를 붙이거나 따옴표로
    감싸거나 여러 줄로 답한다. 그대로 BM25 에 넘기면 기호·조사가 토큰이 되어
    검색어가 오염된다 — 토크나이저가 그것들을 그대로 토큰으로 만들기 때문이다.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("한도제한계좌 해제 서류", "한도제한계좌 해제 서류"),
            ("검색어: 한도제한계좌 해제 서류", "한도제한계좌 해제 서류"),
            ('"한도제한계좌 해제 서류"', "한도제한계좌 해제 서류"),
            ("  한도제한계좌 해제 서류  \n", "한도제한계좌 해제 서류"),
            # 여러 줄이면 뒤쪽은 대개 설명이다 — 첫 줄만 취한다
            ("한도제한계좌 해제 서류\n이 검색어는 ...", "한도제한계좌 해제 서류"),
            ("Search terms: E-9 외국인등록증", "E-9 외국인등록증"),
            ("", ""),
        ],
    )
    def test_cleaning(self, raw, expected):
        from app.llm.local_client import _clean_search_terms

        assert _clean_search_terms(raw) == expected

    def test_visa_code_survives(self):
        """체류자격 코드가 살아남아야 한다 — BM25 변별력의 핵심이다."""
        from app.llm.local_client import _clean_search_terms

        assert "E-9" in _clean_search_terms("검색어: E-9 비전문취업 계좌개설")

"""로컬 생성 백엔드 — 외부 호출 없음 (ADR-004).

왜 로컬인가
-----------
ADR-002 는 "감수하는 것"으로 **국내 리전 원칙 미충족**을 적어 두었다.
Anthropic API 가 해외 리전이기 때문이다. 생성을 로컬 모델로 내리면 그 항목이
문구 수정이 아니라 **실제로 충족**된다 — 금융 도메인에서 데이터 주권은
서술로 덮을 수 있는 종류의 것이 아니다.

`LLMClient` 프로토콜만 만족하면 파이프라인은 손댈 필요가 없다. planner §2.3 이
"교체 비용 0"을 노리고 경계를 먼저 그어 둔 것이 여기서 쓰인다.

구조화 출력
-----------
**이 백엔드의 성패는 여기서 갈린다.** Anthropic 경로는 structured output 이
스키마를 강제해 주지만, 로컬 모델은 그런 보장이 없다. `citations` 나
`numbers_used` 가 깨지면 후처리의 인용 검사·숫자 대조가 통째로 무력해진다.

그래서 **XGrammar** 로 문법을 강제한다. 문법 제약은 형식적으로 잘못된 출력을
불가능하게 만들므로, 남는 위험은 형식이 아니라 **내용**(인용이 맞는가,
수치를 빠짐없이 나열했는가)으로 옮겨간다 — 그건 골든셋이 재는 것이다.

⚠ 문법은 **배열이 있다는 것만** 강제한다. `numbers_used` 의 *완전성*은
강제하지 못한다. 누락되면 숫자 대조가 "통과는 하되 아무것도 못 잡는다"
(ADR-002) — 환각 방어가 조용히 꺼진다. 골든셋 `gold_numbers` 로 별도 확인한다.

⚠ 스키마는 프롬프트에 **주입되지 않는다.** 모델은 스키마를 보지 못하므로
기대 구조를 프롬프트 본문에 서술해야 한다 (`app/llm/prompts.py`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from app.llm.base import (
    GenerationFailed,
    GenerationResult,
    GenerationStats,
    StreamEvent,
)
from app.schemas.common import Lang
from app.schemas.llm import LLMAnswer

log = logging.getLogger(__name__)

# 정규화는 짧은 검색어만 만들면 된다 (anthropic_client 의 NORMALIZE_MAX_TOKENS 와 같은 값).
NORMALIZE_MAX_NEW_TOKENS = 128

# 소형 모델은 "설명 없이 검색어만"이라고 해도 서두를 붙이거나 따옴표로 감싸는
# 일이 잦다. 그대로 BM25 에 넣으면 검색어가 오염된다 — 토크나이저가 조사·
# 기호를 그대로 토큰으로 만들기 때문이다.
_PREFIXES = ("검색어:", "검색어 :", "Search terms:", "Keywords:", "답변:", "출력:")


def resolve_device(preference: str = "auto") -> str:
    """실행 장치를 고른다.

    개발은 mps(Apple Silicon), 배포는 cuda(AWS g5/g6). **같은 코드 경로**로
    양쪽을 돌려야 "로컬에선 됐는데 배포에서는" 사고를 피할 수 있다 —
    이 프로젝트가 컨테이너 기동 실패와 조판에서 이미 두 번 겪은 형태다.
    """
    if preference != "auto":
        return preference
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(device: str, preference: str = "auto") -> Any:
    """장치별 기본 dtype.

    mps 는 bfloat16 지원이 고르지 않아 float16 을 쓴다. cpu 에서 float16 은
    오히려 느리므로 float32 로 둔다.
    """
    import torch

    if preference != "auto":
        return getattr(torch, preference)
    if device == "cuda":
        return torch.bfloat16
    if device == "mps":
        return torch.float16
    return torch.float32


def _clean_search_terms(raw: str) -> str:
    """모델 출력에서 검색어만 남긴다.

    "설명 없이 검색어만 출력하세요"라고 지시해도 소형 모델은 서두("검색어:")를
    붙이거나 따옴표로 감싸거나 여러 줄로 답한다. 그대로 BM25 에 넘기면 기호와
    조사가 토큰이 되어 검색어가 오염된다.

    **첫 줄만 취한다.** 여러 줄이 오면 뒤쪽은 대개 설명이다.
    """
    text = (raw or "").strip()
    for line in text.splitlines():
        line = line.strip()
        if line:
            text = line
            break
    else:
        return ""

    for p in _PREFIXES:
        if text.lower().startswith(p.lower()):
            text = text[len(p):].strip()
    return text.strip().strip('"“”\'`').strip()


class LocalLLMClient:
    """transformers + XGrammar 기반 생성 클라이언트.

    모델을 **지연 로드**한다. 임포트 시점에 수 GB 를 올리면 테스트와 CLI 가
    전부 느려지고, 이 백엔드를 쓰지 않는 설정에서도 메모리를 먹는다.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        dtype: str | None = None,
    ) -> None:
        from app.config import get_settings

        s = get_settings()
        self.model_name = model_name or s.local_model
        self.device = resolve_device(device or s.local_device)
        self._dtype_pref = dtype or s.local_dtype
        self._model = None
        self._tokenizer = None
        self._compiler = None  # XGrammar GrammarCompiler

    # ── 지연 로드 ────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype = resolve_dtype(self.device, self._dtype_pref)
        started = time.perf_counter()
        log.info("로컬 모델 로드: %s (device=%s dtype=%s)", self.model_name, self.device, dtype)

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name, dtype=dtype,
        ).to(self.device)
        self._model.eval()
        torch.set_grad_enabled(False)
        log.info("로컬 모델 로드 완료 — %.1f초", time.perf_counter() - started)

    @property
    def compiler(self):
        """XGrammar 컴파일러. 토크나이저 어휘에 묶이므로 모델당 하나다."""
        if self._compiler is None:
            import xgrammar as xgr

            self._load()
            info = xgr.TokenizerInfo.from_huggingface(
                self._tokenizer, vocab_size=self._model.config.vocab_size,
            )
            self._compiler = xgr.GrammarCompiler(info)
        return self._compiler

    # ── LLMClient 프로토콜 ───────────────────────────────────────────

    async def stream(
        self, *, system: str, user: str, lang: Lang,
    ) -> AsyncIterator[StreamEvent]:
        """구조화 출력 생성. Step 2 에서 구현한다."""
        raise GenerationFailed("로컬 생성은 아직 구현되지 않았습니다 (Step 2)")
        yield  # pragma: no cover - 시그니처를 제너레이터로 유지

    async def translate_to_search_terms(self, query: str, lang: str) -> str:
        """질의 정규화 — 비한국어 질의를 한국어 검색어로.

        여기서 실패해도 사전(glossary) 경로가 남으므로 파이프라인이 죽지 않는다.
        위험이 가장 작은 지점이라 런타임 배선을 이 메서드로 먼저 검증한다.

        **greedy 로 뽑는다.** 검색어 생성은 창의성이 필요한 작업이 아니고,
        같은 질의가 매번 다른 검색어가 되면 회귀 측정이 불가능해진다.
        """
        import asyncio

        from app.llm.prompts import normalize_prompt

        prompt = normalize_prompt(query, lang)
        try:
            text = await asyncio.to_thread(self._generate_text, prompt, NORMALIZE_MAX_NEW_TOKENS)
        except Exception as e:  # noqa: BLE001 - 원인을 프로토콜 예외로 좁힌다
            raise GenerationFailed(f"로컬 정규화 실패: {type(e).__name__}: {e}") from e
        return _clean_search_terms(text)

    # ── 내부 ─────────────────────────────────────────────────────────

    def _generate_text(self, prompt: str, max_new_tokens: int) -> str:
        """동기 생성. 호출부가 `asyncio.to_thread` 로 감싼다.

        transformers 의 `generate` 는 블로킹이라 이벤트 루프에서 직접 부르면
        SSE 스트림 전체가 멎는다.
        """
        self._load()
        messages = [{"role": "user", "content": prompt}]
        # `return_dict=True` 로 받아 **attention_mask 를 함께 넘긴다.** 빼면
        # pad 와 eos 가 같은 토큰이라 모델이 마스크를 추론하지 못하고,
        # transformers 가 "unexpected behavior" 를 경고한다.
        enc = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt",
            return_dict=True,
        ).to(self.device)

        out = self._model.generate(
            **enc,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=self._tokenizer.eos_token_id,
        )
        # 프롬프트 부분을 잘라내고 새로 생성된 토큰만 디코딩한다.
        prompt_len = enc["input_ids"].shape[-1]
        return self._tokenizer.decode(out[0][prompt_len:], skip_special_tokens=True)


def build_local_client() -> LocalLLMClient:
    return LocalLLMClient()


__all__ = ["LocalLLMClient", "build_local_client", "resolve_device", "resolve_dtype"]

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


def resolve_vocab_size(config, tokenizer) -> int:
    """XGrammar 에 넘길 어휘 크기를 찾는다.

    ★ **`config.vocab_size` 만 읽으면 안 된다.** transformers 5 부터 신형 아키텍처는
    텍스트 설정을 `config.text_config` 로 한 겹 감싼다. 실측(2026-08-17):

        Qwen/Qwen3.5-4B                 text_config.vocab_size = 248,320
        google/gemma-4-e4b-it           text_config.vocab_size = 262,144
        aisingapore/…-4B-VL             text_config.vocab_size = 151,936
        sail/Sailor2-8B-Chat (Qwen2)    vocab_size            = 151,936  ← 옛 방식

    처음에는 VL 계열의 특성으로 봤지만 그렇지 않다 — **신형 config 스키마 전반**이다.
    틀린 값을 넘기면 `TokenizerInfo` 의 마스크가 로짓과 어긋나는데 **에러가 나지
    않는다.** 문법 제약이 조용히 꺼지고, 그러면 `citations`·`numbers_used` 가 깨져
    인용 검사와 숫자 대조가 통째로 무력해진다 — ADR-004 ① 과 같은 형태의 사고다.

    어휘 크기는 **모델의 로짓 차원**이어야 한다(마스크가 로짓 위에서 돈다).
    토크나이저 길이는 패딩 때문에 다를 수 있으므로 최후의 수단으로만 쓴다.
    """
    for holder, attr in ((config, "vocab_size"),
                         (getattr(config, "text_config", None), "vocab_size")):
        size = getattr(holder, attr, None) if holder is not None else None
        if isinstance(size, int) and size > 0:
            return size
    size = len(tokenizer)
    log.warning("config 에서 vocab_size 를 찾지 못해 토크나이저 길이(%d)를 씁니다. "
                "패딩 때문에 로짓 차원과 다를 수 있습니다 — 문법 제약이 어긋나면 "
                "이 값을 먼저 의심하세요.", size)
    return size


def _require_all_fields(schema: dict) -> dict:
    """모든 object 의 속성을 `required` 로 만든다 — **문법에서만.**

    ★ 이걸 안 하면 안전장치가 조용히 꺼진다. `LLMAnswer.citations` 는
    `default_factory=list` 라 JSON 스키마의 `required` 에 들어가지 않고,
    문법은 **선택 필드의 생략을 허용**한다. 실측(Qwen3-4B): 모델이
    `answer`·`tier`·`numbers_used` 는 채우고 `citations` 를 통째로 건너뛰었다.
    → 인용 0건 → 후처리가 차단 → 정상 답변이 `no_citation` 폴백이 된다.

    Anthropic 경로는 같은 스키마로도 문제가 없었다. Claude 가 지시를 따라
    채웠기 때문이지 스키마가 강제해서가 아니다 — **모델의 선의에 기대고
    있던 자리**다.

    Pydantic 모델은 건드리지 않는다. 파이썬 쪽 기본값은 그대로 두고(입력이
    없을 때의 관용은 유지), 생성 문법만 엄격하게 만든다.
    """
    def walk(node):
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items()}
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"].keys())
                node.setdefault("additionalProperties", False)
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


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
        self._answer_grammar = None  # 컴파일된 LLMAnswer 문법

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
        # ★ 로드 경로가 **장치마다 다르다.** 둘 다 실측으로 정해진 값이다
        #   (2026-08-17, M3 Pro 18GB).
        #
        #   `.to(device)` 는 최대 메모리를 **두 배로** 만든다. from_pretrained 가
        #   먼저 CPU 에 전체를 올리고, `.to()` 가 대상 장치에 복사본을 만드는 동안
        #   둘이 함께 존재한다. 8B fp16(15.9GB)에서 순간 ~32GB 가 필요해 OOM 으로
        #   죽었다 — 가중치 로드는 63초에 성공해 있었고 바로 그 다음이었다.
        #
        #   `device_map` 은 accelerate 가 샤드 단위로 대상 장치에 바로 올려 그
        #   두 배를 없앤다. **그런데 MPS 에서는 세그폴트(SIGSEGV)가 난다** — 로드
        #   도중 조용히 죽고 파이썬 예외가 없어 원인을 찾기 어렵다. 4B 로도 재현된다.
        #
        #   그래서 cuda 만 device_map 을 쓴다. 최대 메모리가 문제가 되는 곳도
        #   거기다 — 24GB GPU 에 9B(~19GB)를 `.to()` 로 올리면 38GB 가 필요해
        #   똑같이 죽는다. mps 는 8B 가 애초에 안 들어가므로 두 배를 감수해도 잃는
        #   것이 없다.
        kwargs = {"dtype": dtype}
        if self.device == "cuda":
            kwargs["device_map"] = self.device
        self._model = AutoModelForCausalLM.from_pretrained(self.model_name, **kwargs)
        if "device_map" not in kwargs:
            self._model = self._model.to(self.device)
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
                self._tokenizer,
                vocab_size=resolve_vocab_size(self._model.config, self._tokenizer),
            )
            self._compiler = xgr.GrammarCompiler(info)
        return self._compiler

    # ── LLMClient 프로토콜 ───────────────────────────────────────────

    @property
    def answer_grammar(self):
        """`LLMAnswer` 스키마를 강제하는 컴파일된 문법.

        컴파일은 비싸므로 한 번만 한다. 스키마가 바뀌면 프로세스를 다시 띄워야
        하지만, 스키마는 배포 단위로 고정이라 문제가 되지 않는다.
        """
        if self._answer_grammar is None:
            import json

            self._answer_grammar = self.compiler.compile_json_schema(
                json.dumps(_require_all_fields(LLMAnswer.model_json_schema()))
            )
        return self._answer_grammar

    async def stream(
        self, *, system: str, user: str, lang: Lang,
    ) -> AsyncIterator[StreamEvent]:
        """구조화 출력을 스트리밍한다.

        문법 제약이 형식을 보장하므로 파싱 실패는 사실상 사라진다. 남는 위험은
        **내용**이다 — 인용 번호가 맞는가, `numbers_used` 를 빠짐없이 나열했는가.
        후처리(`app.tiering.postprocess`)가 그 둘을 검사하고, 골든셋이 잰다.
        """
        import asyncio
        import queue
        import threading

        from app.streaming.partial_json import AnswerStreamer

        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        chunks: queue.Queue = queue.Queue()

        def run() -> None:
            """생성은 블로킹이라 스레드에서 돌린다. 조각은 큐로 넘긴다."""
            try:
                for piece in self._generate_stream(system, user):
                    loop.call_soon_threadsafe(chunks.put, piece)
            except Exception as e:  # noqa: BLE001 - 호출부로 원인을 넘긴다
                loop.call_soon_threadsafe(chunks.put, e)
            finally:
                loop.call_soon_threadsafe(chunks.put, None)

        threading.Thread(target=run, daemon=True).start()

        streamer = AnswerStreamer()
        raw_parts: list[str] = []
        while True:
            item = await asyncio.to_thread(chunks.get)
            if item is None:
                break
            if isinstance(item, Exception):
                raise GenerationFailed(
                    f"로컬 생성 실패: {type(item).__name__}: {item}"
                ) from item
            raw_parts.append(item)
            # `answer` 가 스키마 첫 필드라 본문이 가장 먼저 흘러나온다.
            if piece := streamer.feed(item):
                yield StreamEvent(kind="token", text=piece)

        raw = "".join(raw_parts)
        try:
            answer = LLMAnswer.model_validate_json(raw)
        except Exception as e:
            # 문법 제약이 있으므로 여기 오면 대개 생성 길이 초과다.
            raise GenerationFailed(
                f"구조화 출력 파싱 실패: {type(e).__name__} (길이 {len(raw)})"
            ) from e

        yield StreamEvent(
            kind="final",
            result=GenerationResult(
                answer=answer,
                raw_text=raw,
                stats=GenerationStats(
                    model=self.model_name,
                    output_tokens=len(raw_parts),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
            ),
        )

    def _generate_stream(self, system: str, user: str):
        """문법 제약 스트리밍 생성 (동기 제너레이터)."""
        from transformers import TextIteratorStreamer
        from xgrammar.contrib.hf import LogitsProcessor

        self._load()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        enc = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt",
            return_dict=True,
        ).to(self.device)

        text_streamer = TextIteratorStreamer(
            self._tokenizer, skip_prompt=True, skip_special_tokens=True,
        )
        import threading

        from app.config import get_settings

        kwargs = dict(
            **enc,
            max_new_tokens=get_settings().local_max_new_tokens,
            do_sample=False,
            pad_token_id=self._tokenizer.eos_token_id,
            logits_processor=[LogitsProcessor(self.answer_grammar)],
            streamer=text_streamer,
        )
        t = threading.Thread(target=self._model.generate, kwargs=kwargs, daemon=True)
        t.start()
        yield from text_streamer

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


__all__ = ["LocalLLMClient", "build_local_client", "resolve_device", "resolve_dtype",
           "resolve_vocab_size"]

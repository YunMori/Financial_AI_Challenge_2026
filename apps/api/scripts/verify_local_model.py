"""로컬 생성 모델 배선 검증 — GPU 시간을 사기 전에 리스크를 턴다 (ADR-004).

    python apps/api/scripts/verify_local_model.py --model Qwen/Qwen3.5-4B
    python apps/api/scripts/verify_local_model.py --model ... --dry-run   # 가중치 없이

`scripts/verify_generation.py` 가 Anthropic 경로에 대해 하는 일의 로컬 대응물이다.

왜 필요한가
-----------
모델마다 chat template 이 다르고, XGrammar `TokenizerInfo` 가 어긋날 수 있고,
`apply_chat_template(..., return_dict=True)` 가 안 되는 모델도 있다. 이걸 AWS 에서
처음 마주치면 **인스턴스 요금을 태우면서 디버깅**하게 된다. 후보당 수 분이면
끝나는 검사를 먼저 돌린다.

무엇을 보는가
-------------
    1. 리포 접근성        gated 면 401 — 가중치를 받기도 전에 알 수 있다
    2. 모델 클래스        VL·MoE 계열은 AutoModelForCausalLM 으로 안 열린다
    3. chat template      없으면 프롬프트 조립이 통째로 실패한다
    4. vocab_size 위치    VL 은 config.text_config 아래다 ★문법이 조용히 깨진다
    5. XGrammar 컴파일    LLMAnswer 스키마가 이 토크나이저에서 컴파일되는가
    6. 문법 제약 생성     6개 필드가 다 채워지는가 (특히 citations)

★ 4번이 조용한 실패다. `TokenizerInfo` 의 vocab_size 가 틀리면 문법 제약이
  어긋나는데 **에러가 나지 않는다** — ADR-004 의 `citations` 생략 사고와 같은 형태다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.llm import LLMAnswer  # noqa: E402

OK, BAD, WARN = "✅", "❌", "⚠️ "

# 짧은 근거 하나로 6개 필드를 다 채우게 만드는 최소 과제.
PROBE_SYSTEM = (
    "당신은 근거만으로 답하는 안내 도우미입니다. 아래 JSON 스키마로만 답하십시오.\n"
    '{"answer": 문자열(한국어 1~2문장), "tier": "A"|"B"|"C", '
    '"citations": [{"ref": 정수, "used_for": 문자열}], '
    '"numbers_used": [문자열], "needs_confirmation": 불리언, "out_of_scope": 불리언}\n'
    "answer 에 등장시킨 모든 수치를 numbers_used 에 빠짐없이 나열하고, "
    "사용한 근거 번호를 citations 에 기록합니다."
)
PROBE_USER = (
    "<context>\n"
    "[근거 1] 금융위원회 「한도제한계좌 개선」 (발행 2024-05-02 / 확인 2026-08-12)\n"
    "한도제한계좌는 하루에 인터넷뱅킹 100만원까지 이체할 수 있다.\n"
    "</context>\n\n"
    "질문: 한도제한계좌의 하루 인터넷뱅킹 이체 한도는 얼마인가요?"
)


def check(label: str, fn):
    """검사 하나. 예외는 잡아서 계속 진행한다 — 첫 실패에서 멈추면 나머지 배선을
    한 번에 못 본다."""
    try:
        detail = fn()
        print(f"  {OK} {label}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  {BAD} {label} — {type(e).__name__}: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="HF 리포 ID")
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    # 배선 검증은 **품질 측정이 아니다.** Gemma 계열은 bf16 학습이라 MPS 기본값인
    # fp16 으로 재면 품질에 dtype 아티팩트가 섞이는데, "로드되는가 · 문법이 먹는가"
    # 만 볼 때는 dtype 을 명시해 그 변수를 아예 뺄 수 있다.
    ap.add_argument("--dtype", default="auto",
                    choices=["auto", "float16", "bfloat16", "float32"])
    ap.add_argument("--dry-run", action="store_true",
                    help="가중치를 받지 않고 리포 메타데이터·설정만 본다")
    ap.add_argument("--max-new-tokens", type=int, default=256,
                    help="탐침 생성 길이. 짧게 잡는다 — 품질이 아니라 배선을 본다")
    args = ap.parse_args()

    print(f"\n모델 {args.model}\n{'─' * 62}")
    state: dict = {}

    # ── 1. 리포 접근성 ───────────────────────────────────────────────
    # 로컬 경로는 이 검사가 성립하지 않는다 — 게이팅도 라이선스 태그도 허브의
    # 개념이다. `export_local_model.py` 의 산출물처럼 우리가 만든 디렉터리를
    # 넘길 때는 건너뛴다. 원본 리포는 이미 이 검사를 통과했다.
    if Path(args.model).is_dir():
        print(f"  {WARN}리포 접근성 — 로컬 경로라 건너뛴다")
    else:
        def repo():
            from huggingface_hub import model_info
            i = model_info(args.model)
            state["gated"] = i.gated
            lic = next((t for t in (i.tags or []) if t.startswith("license:")), "license:?")
            if i.gated:
                raise RuntimeError(f"gated={i.gated} — HF 토큰이 필요하다 "
                                   f"(Gemma 3 가 401 로 막혔던 것과 같은 형태)")
            return f"non-gated · {lic.split(':', 1)[1]}"

        if not check("리포 접근성", repo):
            return 1

    # ── 2. 설정 · vocab_size 위치 ★ ──────────────────────────────────
    def cfg():
        from transformers import AutoConfig, AutoTokenizer

        from app.llm.local_client import resolve_vocab_size

        c = AutoConfig.from_pretrained(args.model)
        state["config"] = c
        arch = ",".join(getattr(c, "architectures", None) or ["?"])
        top = getattr(c, "vocab_size", None)
        nested = getattr(getattr(c, "text_config", None), "vocab_size", None)
        # 런타임과 **같은 해석기**로 푼다. 여기서만 맞고 런타임에서 틀리면 의미가 없다.
        size = resolve_vocab_size(c, AutoTokenizer.from_pretrained(args.model))
        state["vocab_size"] = size
        if size <= 0:
            raise RuntimeError("vocab_size 를 찾지 못했다")
        where = "config" if top else ("config.text_config" if nested else "토크나이저 길이")
        return f"{arch} · vocab {size:,} ({where})"

    ok_cfg = check("모델 설정 · vocab_size 해석", cfg)

    # ── 3. chat template ─────────────────────────────────────────────
    def tmpl():
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(args.model)
        state["tokenizer"] = tok
        if not getattr(tok, "chat_template", None):
            raise RuntimeError("chat_template 이 없다 — 프롬프트 조립이 실패한다")
        enc = tok.apply_chat_template(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            add_generation_prompt=True, return_tensors="pt", return_dict=True,
        )
        if "attention_mask" not in enc:
            raise RuntimeError("return_dict=True 인데 attention_mask 가 없다")
        state["supports_system"] = True
        return f"system+user OK · {enc['input_ids'].shape[-1]} 토큰"

    ok_tmpl = check("chat template (system 역할 · attention_mask)", tmpl)

    if args.dry_run:
        print(f"\n{WARN}--dry-run: 가중치를 받지 않았다. XGrammar 컴파일과 생성은 "
              f"건너뛴다.\n")
        return 0 if (ok_cfg and ok_tmpl) else 1

    # ── 4. 가중치 로드 ───────────────────────────────────────────────
    def load():
        from app.llm.local_client import LocalLLMClient
        c = LocalLLMClient(model_name=args.model, device=args.device, dtype=args.dtype)
        t0 = time.perf_counter()
        c._load()  # noqa: SLF001 — 배선 검증이 목적이다
        state["client"] = c
        from app.llm.local_client import resolve_dtype
        dt = resolve_dtype(c.device, args.dtype)
        return f"device={c.device} · dtype={str(dt).replace('torch.', '')} · " \
               f"{time.perf_counter() - t0:.0f}초"

    if not check("가중치 로드 (AutoModelForCausalLM)", load):
        print(f"\n  {WARN}VL·MoE 계열은 다른 모델 클래스가 필요하다 — "
              f"local_client.py:183 을 고쳐야 한다.\n")
        return 1

    # ── 5. XGrammar 컴파일 ───────────────────────────────────────────
    def grammar():
        c = state["client"]
        t0 = time.perf_counter()
        c.answer_grammar  # noqa: B018 — 지연 컴파일을 강제한다
        req = json.loads(json.dumps(LLMAnswer.model_json_schema()))
        return (f"{len(req['properties'])}개 필드 · "
                f"{time.perf_counter() - t0:.1f}초")

    ok_gram = check("XGrammar 문법 컴파일", grammar)

    # ── 6. 문법 제약 생성 ★ ──────────────────────────────────────────
    def generate():
        import asyncio

        from app.config import get_settings
        from app.schemas.common import Lang

        get_settings().local_max_new_tokens = args.max_new_tokens

        async def run():
            out = None
            async for ev in state["client"].stream(
                system=PROBE_SYSTEM, user=PROBE_USER, lang=Lang.KO):
                if ev.kind == "final":
                    out = ev.result
            return out

        t0 = time.perf_counter()
        res = asyncio.run(run())
        if res is None:
            raise RuntimeError("final 이벤트가 오지 않았다")
        a: LLMAnswer = res.answer
        state["answer"] = a
        secs = time.perf_counter() - t0
        return f"{secs:.0f}초 · tier={a.tier.value}"

    ok_gen = check("문법 제약 생성 (탐침 1건)", generate)

    # ── 결과 판정 ────────────────────────────────────────────────────
    print()
    if ok_gen:
        a: LLMAnswer = state["answer"]
        print("  생성 결과")
        print(f"    answer        {a.answer[:70]}{'…' if len(a.answer) > 70 else ''}")
        print(f"    citations     {[c.ref for c in a.citations] or '★ 비었다'}")
        print(f"    numbers_used  {a.numbers_used or '★ 비었다'}")
        print(f"    needs_confirm {a.needs_confirmation} · out_of_scope {a.out_of_scope}")
        print()
        # ★ `citations` 는 default_factory=list 라 JSON 스키마의 required 에 들어가지
        #   않는다. `_require_all_fields` 가 문법에서만 강제하는데, 그게 실제로
        #   먹었는지는 여기서만 확인된다 (ADR-004 ①).
        if not a.citations:
            print(f"  {BAD} citations 가 비었다 — `_require_all_fields` 가 먹지 않았다.")
            print("     후처리가 no_citation 으로 차단하므로 **정상 답변이 전부 폴백**된다.")
            ok_gen = False
        if not a.numbers_used:
            print(f"  {WARN}numbers_used 가 비었다. 문법은 배열의 존재만 강제하고 "
                  "완전성은 못 잡는다 —")
            print("     비어 있으면 숫자 대조가 '통과는 하되 아무것도 못 잡는' 상태다.")
        if len(a.answer) > 400:
            print(f"  {WARN}answer 가 {len(a.answer)}자다(목표 400자 이내). 길면 "
                  "뒤따르는 필드가")
            print("     생성 한도에 걸려 잘리고, 결국 no_citation 폴백이 된다.")

    passed = ok_cfg and ok_tmpl and ok_gram and ok_gen
    print(f"\n{'─' * 62}")
    print(f"{OK + ' 배선 이상 없음 — 골든셋 채점으로 넘어가도 된다.' if passed else BAD + ' 배선 문제가 있다 — GPU 를 켜기 전에 고친다.'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Anthropic 구조화 출력 스키마 변환 (`_strict_schema`).

한 `LLMAnswer` 스키마를 두 백엔드가 다르게 받아들인다:

- 로컬(XGrammar)은 `maxItems` 를 문법 차원에서 **강제한다** — ADR-005 후속 ⑦ 이
  `numbers_used` 무한 반복으로 JSON 이 잘리던 문제를 그렇게 고쳤다.
- Anthropic 구조화 출력은 배열의 `maxItems` 를 **거부한다**(400).

그래서 상한은 Pydantic 모델에 남기고, API 경로에서만 떼어 낸다. 이 테스트는
그 두 성질이 동시에 유지되는지 본다 — 한쪽만 보면 다른 쪽이 조용히 깨진다.
"""

from __future__ import annotations

import json

from app.llm.anthropic_client import _strict_schema
from app.schemas.llm import LLMAnswer


def _keys(node: object) -> set[str]:
    """스키마 트리에 등장하는 모든 키."""
    out: set[str] = set()
    if isinstance(node, dict):
        out |= set(node)
        for v in node.values():
            out |= _keys(v)
    elif isinstance(node, list):
        for v in node:
            out |= _keys(v)
    return out


def test_pydantic_모델은_상한을_유지한다():
    """로컬 백엔드가 의존하는 성질이다. 여기가 비면 XGrammar 강제가 사라진다."""
    assert "maxItems" in _keys(LLMAnswer.model_json_schema())


def test_api_스키마에서_상한이_제거된다():
    """남겨 두면 매 호출이 400 으로 죽는다 (실측 2026-09-07).

    `output_config.format.schema: For 'array' type, property 'maxItems'
    is not supported`
    """
    converted = _strict_schema(LLMAnswer.model_json_schema())
    assert "maxItems" not in _keys(converted)
    assert "minItems" not in _keys(converted)


def test_additionalProperties_는_그대로_붙는다():
    """상한 제거가 기존 변환을 망가뜨리지 않았는지."""
    converted = _strict_schema(LLMAnswer.model_json_schema())
    assert converted["additionalProperties"] is False
    for defn in converted.get("$defs", {}).values():
        if defn.get("type") == "object":
            assert defn["additionalProperties"] is False


def test_필수_필드가_보존된다():
    """`citations`·`numbers_used` 가 스키마에서 사라지면 안 된다 — 상한만 뗀다."""
    converted = _strict_schema(LLMAnswer.model_json_schema())
    props = converted["properties"]
    assert {"answer", "tier", "citations", "numbers_used"} <= set(props)
    assert props["citations"]["type"] == "array"
    assert props["numbers_used"]["type"] == "array"


def test_직렬화가_가능하다():
    """실제로 요청 본문에 실리는 형태다."""
    json.dumps(_strict_schema(LLMAnswer.model_json_schema()))

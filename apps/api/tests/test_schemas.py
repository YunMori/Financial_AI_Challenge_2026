"""스키마 계약 테스트.

Pydantic 모델은 기능명세서의 소스이자(planner §1.2) LLM 구조화 출력의
스키마다. 여기서 깨지면 명세서와 실제 동작이 조용히 어긋난다.
"""

import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    MAX_HISTORY_TURNS,
    ChatRequest,
    Purpose,
    SessionContext,
    VisaCode,
)
from app.schemas.llm import LLMAnswer


class TestLLMAnswerContract:
    def test_answer_is_first_field(self):
        """구조화 출력은 스키마 순서대로 생성된다.

        `answer` 가 첫 필드가 아니면 tier·citations 가 다 생성될 때까지
        이용자는 빈 화면을 본다 — TTFT 1.8초 목표(§14.2)가 여기 걸려 있다.
        필드를 추가할 때 실수로 앞에 끼워 넣는 것을 막는다.
        """
        fields = list(LLMAnswer.model_json_schema()["properties"])
        assert fields[0] == "answer", (
            f"answer 가 첫 필드여야 스트리밍이 성립한다. 현재 순서: {fields}"
        )

    def test_json_schema_is_serializable(self):
        """Anthropic structured output 으로 그대로 넘어갈 수 있어야 한다."""
        import json

        schema = LLMAnswer.model_json_schema()
        json.dumps(schema)  # 순환 참조·비직렬화 타입이 있으면 여기서 터진다
        assert set(schema["properties"]) == {
            "answer",
            "tier",
            "citations",
            "numbers_used",
            "needs_confirmation",
            "out_of_scope",
        }

    def test_defaults_allow_tier_c_shape(self):
        """계층 C 는 answer 를 쓰지 않고 out_of_scope 만 세운다."""
        a = LLMAnswer(answer="", tier="C", out_of_scope=True)
        assert a.citations == [] and a.numbers_used == []


class TestSessionContext:
    def test_all_fields_optional(self):
        """프로필 없이도 일반 안내는 가능해야 한다. 강제하면 첫 화면에서 이탈한다."""
        assert SessionContext().visa is None

    def test_nationality_uppercased(self):
        assert SessionContext(nationality="vn").nationality == "VN"

    @pytest.mark.parametrize("bad", ["V", "VNM", ""])
    def test_nationality_must_be_alpha2(self, bad):
        with pytest.raises(ValidationError):
            SessionContext(nationality=bad)

    def test_rejects_unknown_visa(self):
        """자유 입력이 없다 — 열거값 외는 거절한다."""
        with pytest.raises(ValidationError):
            SessionContext(visa="E-99")

    def test_visa_codes_are_hyphenated(self):
        """공식 문서 표기(E-9)를 정본으로 쓴다. 토크나이저가 E9 로 정규화한다."""
        assert all("-" in v.value for v in VisaCode)
        assert len(VisaCode) == 12


class TestChatRequest:
    def test_history_truncated_not_rejected(self):
        """클라이언트 버그로 히스토리가 길어졌다고 질문을 실패시키지 않는다."""
        req = ChatRequest(
            lang="ko",
            message="질문",
            history=[{"role": "user", "content": str(i)} for i in range(20)],
        )
        assert len(req.history) == MAX_HISTORY_TURNS
        assert req.history[-1].content == "19", "최근 것을 남겨야 한다"

    def test_empty_message_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(lang="ko", message="")

    def test_overlong_message_rejected(self):
        """2,000자는 정상 질의 범위를 크게 넘는다 — 절삭이 아니라 거절."""
        with pytest.raises(ValidationError):
            ChatRequest(lang="ko", message="가" * 2001)

    def test_unsupported_language_rejected(self):
        """검수되지 않은 언어는 공개하지 않는다 (planner §10.3)."""
        with pytest.raises(ValidationError):
            ChatRequest(lang="de", message="질문")

    def test_minimal_request(self):
        req = ChatRequest(lang="vi", message="Tại sao?")
        assert req.context.visa is None and req.history == []

    def test_full_request(self):
        req = ChatRequest(
            lang="vi",
            message="Tại sao tôi chỉ chuyển được 1 triệu won?",
            context={
                "nationality": "VN",
                "visa": "E-9",
                "stay": "1y_2y",
                "purposes": ["salary", "remittance"],
            },
        )
        assert req.context.visa is VisaCode.E9
        assert Purpose.SALARY in req.context.purposes


class TestHealthEndpoint:
    def test_healthz_does_not_load_index(self):
        """웜업 대상이므로 가벼워야 한다.

        여기서 임베딩 모델을 올리면 콜드스타트가 그대로 웜업 요청에 실려
        §15.3 대응이 무력해진다.
        """
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert set(body) == {"status", "version", "llm_configured", "index_present"}

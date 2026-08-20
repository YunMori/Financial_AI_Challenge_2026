"""F4 한도제한계좌 해제 가이드 (planner §10-F4).

F4 는 **생성 경로를 새로 만들지 않는다.** 그래서 이 파일이 지키는 것은
파이프라인 품질이 아니라 세 가지 접합부다.

1. 프로필 → 한국어 질의 조립이 검색에 유리한 형태인가
2. `/chat` 과 **같은 SSE 이벤트 순서**로 나가는가
3. `next_action` 이 `done` 에 실리고, **폴백일 때는 실리지 않는가**

생성 품질은 골든셋(`Q-LIMIT-*` 34문항)이 이미 재고 있다 — 여기서 겹쳐 재지 않는다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import chat as chat_router
from app.routers.guide import build_next_action, build_query
from app.schemas.chat import Purpose, SessionContext, VisaCode
from test_pipeline import FakeRetriever, pipeline


@pytest.fixture
def client(monkeypatch):
    """가짜 파이프라인을 꽂는다.

    F4 는 `/chat` 과 **같은** 파이프라인 인스턴스를 쓰므로
    (`routers.chat._pipeline`), 여기를 바꾸면 두 경로가 함께 격리된다.
    진짜를 부르면 로컬 생성 모델 8GB 를 적재한다 — 단위 테스트가 할 일이 아니다.
    """
    monkeypatch.setattr(chat_router, "_pipeline", pipeline())
    return TestClient(app)


def _events(raw: str) -> list[tuple[str, dict]]:
    """SSE 본문을 (event, data) 목록으로 푼다."""
    out = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.append((name, data))
    return out


class TestQueryAssembly:
    """★ 질의를 우리가 만들기 때문에 ③ 정규화를 통과할 필요가 없다."""

    def test_query_is_korean_regardless_of_answer_language(self):
        q = build_query(SessionContext(visa=VisaCode.E9, purposes=[Purpose.SALARY]))
        assert "한도제한계좌" in q
        assert all(ord(ch) < 0x3000 or "가" <= ch <= "힯" or ch.isspace() or ch == "-"
                   for ch in q), q

    def test_compound_noun_is_kept_whole(self):
        """토크나이저에 복합명사로 등록된 표기를 그대로 쓴다 (rag/tokenize.py)."""
        assert "한도제한계좌" in build_query(SessionContext())

    def test_visa_code_is_included_verbatim(self):
        """BM25 가 E-9 와 E-7 을 다른 토큰으로 다루는 것이 변별의 핵심이다."""
        q = build_query(SessionContext(visa=VisaCode.E9))
        assert "E-9" in q and "E-7" not in q

    def test_only_the_first_purpose_is_used(self):
        """목적을 여러 개 이으면 검색어가 넓어져 top1 이 흐려진다."""
        q = build_query(SessionContext(
            purposes=[Purpose.SALARY, Purpose.REMITTANCE, Purpose.TUITION]))
        assert "급여 수령" in q
        assert "해외 송금" not in q

    def test_works_without_a_profile(self):
        """프로필이 비어도 일반 안내는 가능해야 한다 (F1 의 원칙)."""
        q = build_query(SessionContext())
        assert "한도제한계좌 해제" in q

    @pytest.mark.parametrize("purpose", list(Purpose))
    def test_every_purpose_has_a_korean_surface(self, purpose):
        """열거값이 늘면 여기서 KeyError 로 드러난다 — 조용히 빠지지 않는다."""
        q = build_query(SessionContext(purposes=[purpose]))
        assert len(q.split()) >= 3


class TestNextAction:
    def test_profile_is_passed_through_to_the_checklist(self):
        a = build_next_action(SessionContext(visa=VisaCode.E9, purposes=[Purpose.SALARY]))
        assert a.type == "checklist"
        assert a.params == {"visa": "E-9", "purpose": "salary"}

    def test_empty_profile_yields_empty_params(self):
        assert build_next_action(SessionContext()).params == {}

    def test_label_is_an_i18n_key_not_text(self):
        """문구는 클라이언트가 갖는다 — 서버가 3언어를 또 들고 있지 않는다."""
        assert build_next_action(SessionContext()).label_key == "guide.makeChecklist"


class TestStream:
    def test_content_type_and_headers_match_chat(self, client):
        r = client.post("/api/v1/guide/limit-release",
                        json={"lang": "ko", "context": {"visa": "E-9"}})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # 프록시 버퍼링 방지 — 없으면 배포에서 스트리밍이 죽는다
        assert r.headers["x-accel-buffering"] == "no"

    def test_event_order_ends_with_done(self, client):
        r = client.post("/api/v1/guide/limit-release",
                        json={"lang": "ko", "context": {"visa": "E-9",
                                                        "purposes": ["salary"]}})
        events = _events(r.text)
        assert events, r.text
        assert events[-1][0] == "done"

    def test_next_action_rides_on_done(self, client):
        r = client.post("/api/v1/guide/limit-release",
                        json={"lang": "ko", "context": {"visa": "E-9",
                                                        "purposes": ["salary"]}})
        name, done = _events(r.text)[-1]
        assert name == "done"
        assert done["fallback_reason"] is None
        assert done["next_action"] == {
            "type": "checklist",
            "label_key": "guide.makeChecklist",
            "params": {"visa": "E-9", "purpose": "salary"},
        }

    def test_no_next_action_on_fallback(self, monkeypatch):
        """★ 답하지 못한 뒤에 '이 서류로 체크리스트를 만드세요'는 답한 척이 된다."""
        # 검색 신뢰도를 임계값 아래로 떨어뜨려 low_confidence 폴백을 만든다.
        monkeypatch.setattr(chat_router, "_pipeline",
                            pipeline(retriever=FakeRetriever(top1=0.10)))
        r = TestClient(app).post("/api/v1/guide/limit-release",
                                 json={"lang": "ko", "context": {"visa": "E-9"}})
        name, done = _events(r.text)[-1]
        assert name == "done"
        assert done["fallback_reason"] == "low_confidence"
        assert done["next_action"] is None

    def test_assembled_query_reaches_the_retriever(self, monkeypatch):
        """조립한 한국어 질의가 정규화에 휘둘리지 않고 검색까지 간다."""
        retriever = FakeRetriever()
        monkeypatch.setattr(chat_router, "_pipeline", pipeline(retriever=retriever))
        TestClient(app).post("/api/v1/guide/limit-release",
                             json={"lang": "vi", "context": {"visa": "E-9",
                                                             "purposes": ["salary"]}})
        assert retriever.queries
        assert "한도제한계좌" in retriever.queries[0]

    def test_invalid_language_is_rejected(self, client):
        assert client.post("/api/v1/guide/limit-release",
                           json={"lang": "th"}).status_code == 422

    def test_invalid_visa_is_rejected(self, client):
        assert client.post("/api/v1/guide/limit-release",
                           json={"lang": "ko",
                                 "context": {"visa": "X-1"}}).status_code == 422

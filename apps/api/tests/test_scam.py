"""F8 사기 유형 대조 (planner §9.2, §10-F8).

이 파일이 지키는 주장은 셋이다.

1. **판정하지 않는다.** 응답에 "사기인가"를 담는 필드가 없고, tier 는 항상 C 다.
   데모 시나리오 3 의 핵심이 이 거절이라 스키마 수준에서 굳힌다.
2. **근거 없는 항목은 존재할 수 없다.** 사기 수법 목록은 "여기 없으면 안전하다"로
   읽히기 쉬워, 지어낸 항목 하나가 목록 전체의 신뢰를 무너뜨린다.
3. **조치 순서를 바꾸지 않는다.** 지급정지가 신고보다 먼저인 것은 돈이 빠져나가는
   것을 막는 일이 환급보다 앞서기 때문이다.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.matrix.scam import SCAM_YAML, build_response
from app.schemas.common import Lang, Tier


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def raw():
    return yaml.safe_load(Path(SCAM_YAML).read_text(encoding="utf-8"))


class TestNoJudgement:
    def test_tier_is_always_c(self):
        """개별 사안 판단은 계층 C 다 — 모델도 규칙도 여기서 답하지 않는다."""
        for lang in Lang:
            assert build_response(lang).disclaimer_tier is Tier.C

    def test_response_has_no_verdict_field(self):
        """★ 판정 필드가 **없어야** 한다.

        스키마에 자리를 만들어 두면 언젠가 누군가 채운다. 필드가 없다는 것이
        설계이며, 이 테스트가 그 자리를 지킨다.
        """
        fields = set(build_response(Lang.KO).model_dump().keys())
        for banned in ("verdict", "is_scam", "judgement", "score", "risk"):
            assert banned not in fields

    def test_endpoint_rejects_a_case_question(self, client):
        """대조표 조회에 사연을 실어 보낼 수 있는 인자가 없다."""
        r = client.get("/api/v1/scam", params={"lang": "ko", "message": "이 전화 사기인가요?"})
        assert r.status_code == 200
        # 알 수 없는 쿼리 인자는 무시되고, 응답은 언제나 같은 대조표다.
        assert r.json() == client.get("/api/v1/scam", params={"lang": "ko"}).json()


class TestEvidence:
    def test_every_type_has_evidence(self, raw):
        for t in raw["types"]:
            urls = [e.get("source_url") for e in t.get("evidence") or []]
            assert any(urls), f"{t['code']} 에 근거가 없다"

    def test_every_contact_has_evidence(self, raw):
        for c in raw["contacts"]:
            urls = [e.get("source_url") for e in c.get("evidence") or []]
            assert any(urls), f"{c['code']} 에 근거가 없다"

    def test_evidence_doc_ids_exist_in_corpus(self, raw):
        """근거로 적은 doc_id 가 실제 코퍼스 문서여야 한다.

        오타 하나로 "출처 있음"이 거짓이 되는 것을 막는다.
        """
        # SCAM_YAML = <base>/corpus/matrix/scam.yaml → parents[1] 이 corpus/
        processed = Path(SCAM_YAML).parents[1] / "processed"
        if not processed.is_dir():  # pragma: no cover - 이미지에는 processed 가 없다
            pytest.skip("corpus/processed 가 없는 환경")
        known = {p.stem for p in processed.glob("*.md")}
        blocks = raw["types"] + raw["contacts"] + [{"evidence": raw["response_evidence"]}]
        for b in blocks:
            for e in b.get("evidence") or []:
                assert e["doc_id"] in known, f"{e['doc_id']} 는 코퍼스에 없다"

    def test_published_at_is_not_invented(self, raw):
        """근거 3종은 원문에 발행일이 없다 — null 이어야 하고 지어내면 안 된다."""
        blocks = raw["types"] + raw["contacts"] + [{"evidence": raw["response_evidence"]}]
        for b in blocks:
            for e in b.get("evidence") or []:
                assert e.get("published_at") is None


class TestResponseSteps:
    def test_payment_suspension_comes_first(self, raw):
        steps = sorted(raw["response_steps"], key=lambda s: s["seq"])
        assert [s["seq"] for s in steps] == [1, 2, 3]
        assert "지급정지" in steps[0]["label_ko"]
        assert "112" in steps[1]["label_ko"]

    def test_steps_keep_order_in_response(self):
        seqs = [s.seq for s in build_response(Lang.KO).response_steps]
        assert seqs == sorted(seqs)


class TestLocalization:
    @pytest.mark.parametrize("lang", list(Lang))
    def test_all_public_languages_render(self, lang):
        r = build_response(lang)
        assert r.types and r.response_steps and r.contacts
        assert all(t.title and t.body for t in r.types)

    def test_definite_warnings_are_translated(self, raw):
        """★ '100% 사기'라는 단정은 **공개 언어 전부**에 있어야 한다.

        한국어로만 보이면 그 경고가 대상 이용자에게 전달되지 않는다 —
        경고문에서 폴백이 일어나는 것은 기능 상실이다.
        """
        for t in raw["types"]:
            if t["certainty"] != "definite":
                continue
            for lang in Lang:
                if lang is Lang.KO:
                    continue
                assert (t.get("body_i18n") or {}).get(lang.value), (
                    f"{t['code']} 의 {lang.value} 번역이 없다 — 단정 경고는 폴백하면 안 된다"
                )


class TestEndpoint:
    def test_returns_catalog(self, client):
        r = client.get("/api/v1/scam", params={"lang": "vi"})
        assert r.status_code == 200
        body = r.json()
        assert body["lang"] == "vi"
        assert body["disclaimer_tier"] == "C"
        assert len(body["types"]) == len(build_response(Lang.VI).types)

    def test_primary_contact_is_112(self, client):
        body = client.get("/api/v1/scam").json()
        primary = [c for c in body["contacts"] if c["primary"]]
        assert [c["number"] for c in primary] == ["112"]

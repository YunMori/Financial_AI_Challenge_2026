"""요건 매트릭스와 F2 내비게이터 (planner §4.2, §9.2).

이 파일이 지키는 주장은 둘이다.

1. **근거 없이 official 인 셀은 존재할 수 없다.** 화면의 "공식 확인됨" 배지가
   근거 링크 없이 뜨는 것을 로드 시점에 막는다.
2. **미확인은 0 점이 아니다.** 확인하지 못한 항목을 낮은 점수로 접으면
   근거 없는 주장을 정렬 순서로 만들게 된다.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.matrix.fit import (
    W_CHANNEL_ACCESS,
    W_DOC_BURDEN,
    W_VISA_FIT,
    max_required_docs,
    ranking_policy,
    score_institution,
)
from app.matrix.loader import get_matrix, load_matrix
from app.matrix.service import build_response, resolve_docs
from app.schemas.common import EvidenceStatus, Lang, Tier


@pytest.fixture(scope="module")
def matrix():
    return get_matrix()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "institutions.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def _minimal(inst_overrides: dict) -> dict:
    """로더가 요구하는 최소 형태 + 테스트가 바꿀 부분만 덮어쓴다."""
    inst = {
        "inst_code": "BANK_T",
        "inst_name_ko": "테스트은행",
        "inst_name_i18n": {"en": "Test Bank"},
        "foreign_channel": {
            "mobile_arc_accepted": True,
            "mobile_arc_since": "2025-03-21",
            "channels": ["branch"],
            "status": "unknown",
            "evidence": [],
            "dedicated_branch_count": None,
            "languages_supported": [],
            "languages_status": "unknown",
        },
        "visa_rules": [],
    }
    inst["foreign_channel"].update(inst_overrides.pop("foreign_channel", {}))
    inst.update(inst_overrides)
    return {
        "version": 1,
        "updated_at": "2026-08-20",
        "institutions": [inst],
        "common": {
            "limited_account": {
                "status": "official",
                "daily_limits": {
                    "electronic_transfer_krw": 1000000,
                    "atm_krw": 1000000,
                    "branch_krw": 3000000,
                },
                "effective_from": "2024-05-02",
                "notes_ko": "",
                "evidence": [
                    {"doc_id": "FSC-X", "source_url": "https://example.invalid",
                     "published_at": "2024-05-02"}
                ],
            }
        },
    }


class TestLoaderInvariants:
    """★ 근거 없는 official 은 로드 시점에 죽는다."""

    def test_official_without_evidence_is_rejected(self, tmp_path):
        data = _minimal({"foreign_channel": {"status": "official", "evidence": []}})
        with pytest.raises(ValueError, match="evidence"):
            load_matrix(_write_yaml(tmp_path, data))

    def test_visa_cell_official_without_evidence_is_rejected(self, tmp_path):
        data = _minimal({
            "visa_rules": [
                {"visa_code": "E-9", "account_open": "official", "channels": [],
                 "required_docs": [], "purpose_docs": [], "notes_ko": "", "evidence": []}
            ]
        })
        with pytest.raises(ValueError, match="evidence"):
            load_matrix(_write_yaml(tmp_path, data))

    def test_unknown_without_evidence_is_fine(self, tmp_path):
        """unknown 은 근거가 없는 것이 정상이다 — 그게 unknown 의 뜻이다."""
        m = load_matrix(_write_yaml(tmp_path, _minimal({})))
        assert m.institutions[0].status is EvidenceStatus.UNKNOWN

    def test_unknown_cell_status_raises(self, tmp_path):
        data = _minimal({"foreign_channel": {"status": "probably"}})
        with pytest.raises(ValueError, match="알 수 없는 셀 상태"):
            load_matrix(_write_yaml(tmp_path, data))


class TestShippedMatrix:
    """실제 배포되는 `corpus/matrix/institutions.yaml`."""

    def test_all_shipped_cells_satisfy_the_invariant(self, matrix):
        # 로드가 성공했다는 사실 자체가 불변식 통과다. 명시적으로 한 번 더 본다.
        for inst in matrix.institutions:
            if inst.status is EvidenceStatus.OFFICIAL:
                assert inst.evidence, inst.inst_code

    def test_mobile_arc_banks_are_promoted_to_cards(self, matrix):
        """보도자료가 열거한 6개 은행이 그대로 기관 카드가 된다."""
        assert len(matrix.institutions) == 6

    def test_only_shinhan_and_jeonbuk_are_non_face_to_face(self, matrix):
        """비대면은 신한·전북만 명시됐다. 나머지에 붙이면 사실과 다르다."""
        online = {i.inst_code for i in matrix.institutions if i.online_available}
        assert online == {"BANK_SHINHAN", "BANK_JEONBUK"}

    def test_visa_cells_remain_unknown(self, matrix):
        """기관이 official 이어도 체류자격별 요건은 별개다 — 채우지 않았다."""
        for inst in matrix.institutions:
            for rule in inst.visa_rules.values():
                assert rule.account_open is EvidenceStatus.UNKNOWN

    def test_branch_limit_is_3m_not_1m(self, matrix):
        """spec-changes #1 — 창구는 300만원이다."""
        la = matrix.limited_account
        assert la.branch_krw == 3_000_000
        assert la.electronic_transfer_krw == 1_000_000


class TestFitScore:
    """★ 미확인을 0 으로 접지 않는다."""

    def test_unverified_items_are_excluded_not_zeroed(self, matrix):
        """전용지점·언어지원이 미확인이면 `unverified` 로 나오고 분모에서 빠진다."""
        inst = matrix.get("BANK_SHINHAN")
        r = score_institution(inst, "E-9", max_required_docs(matrix.institutions, "E-9"))
        assert "dedicated_branch" in r.unverified
        assert "language_support" in r.unverified
        # 전부 0 으로 접혔다면 visa_fit(0.0) 만 남아 점수가 0.0 이 된다.
        assert r.score and r.score > 0.0

    def test_confirmed_non_face_to_face_is_the_only_differentiator(self, matrix):
        """지금 데이터에서 순위를 가르는 것은 확인된 비대면 여부뿐이다."""
        max_docs = max_required_docs(matrix.institutions, "E-9")
        scored = {i.inst_code: score_institution(i, "E-9", max_docs).score
                  for i in matrix.institutions}
        assert scored["BANK_SHINHAN"] == scored["BANK_JEONBUK"]
        assert scored["BANK_SHINHAN"] > scored["BANK_HANA"]

    def test_empty_required_docs_on_unknown_cell_is_not_a_perfect_score(self, matrix):
        """빈 required_docs 를 '서류 0건'으로 읽으면 모르는 은행이 1위가 된다."""
        max_docs = max_required_docs(matrix.institutions, "E-9")
        r = score_institution(matrix.get("BANK_HANA"), "E-9", max_docs)
        assert "doc_simplicity" not in r.reasons
        assert "required_docs" in r.unverified

    def test_max_docs_counts_confirmed_cells_only(self, matrix):
        assert max_required_docs(matrix.institutions, "E-9") == 0

    def test_ranking_policy_reads_the_constants(self):
        """산식 사본을 두지 않는다 — 공개값이 코드 상수와 같아야 한다."""
        p = ranking_policy()
        assert p["weights"]["visa_fit"] == W_VISA_FIT
        assert p["weights"]["doc_burden"] == W_DOC_BURDEN
        assert p["weights"]["channel_access"] == W_CHANNEL_ACCESS

    def test_ranking_policy_excludes_commercial_inputs(self):
        """중립성 4원칙 — 제휴·수수료가 입력에 없다는 것이 공개 항목이다."""
        assert "제휴" in ranking_policy()["excluded_inputs"]


class TestDocLabels:
    def test_glossary_supplies_three_languages(self):
        docs = resolve_docs(["ARC", "EMPLOYMENT_CERT"], "vi")
        assert docs[0].label == "Thẻ đăng ký người nước ngoài"
        assert docs[0].label_ko == "외국인등록증"
        assert docs[1].label_ko == "재직증명서"

    def test_unknown_code_is_surfaced_not_dropped(self):
        """조용히 사라지면 창구에서 서류 한 장이 비는 것으로 드러난다."""
        docs = resolve_docs(["NO_SUCH_DOC"], "en")
        assert len(docs) == 1
        assert docs[0].code == "NO_SUCH_DOC"


class TestResponse:
    def test_unknown_cells_are_listed_separately(self):
        r = build_response(Lang.VI, "E-9")
        # 카드는 나오되(모바일 ARC 는 official) 요건 미확인은 따로 표시된다.
        assert len(r.results) == 6
        assert set(r.unknown_institutions) == {c.inst_code for c in r.results}

    def test_tier_is_b_not_a(self):
        """계좌개설 요건이 미확인이므로 A 는 나올 수 없다."""
        assert build_response(Lang.VI, "E-9").disclaimer_tier is Tier.B

    def test_sorted_by_fit_score_desc(self):
        scores = [c.fit_score for c in build_response(Lang.EN, "E-9").results]
        assert scores == sorted(scores, key=lambda s: -1.0 if s is None else s, reverse=True)

    def test_localized_names(self):
        vi = {c.inst_code: c.inst_name for c in build_response(Lang.VI, "E-9").results}
        ko = {c.inst_code: c.inst_name for c in build_response(Lang.KO, "E-9").results}
        assert vi["BANK_SHINHAN"] == "Ngân hàng Shinhan"
        assert ko["BANK_SHINHAN"] == "신한은행"

    def test_institution_status_and_account_open_are_separate_fields(self):
        """둘을 합치면 '공식 확인됨'이 실제보다 넓게 읽힌다."""
        card = build_response(Lang.KO, "E-9").results[0]
        assert card.status is EvidenceStatus.OFFICIAL
        assert card.account_open is EvidenceStatus.UNKNOWN


class TestEndpoints:
    def test_institutions_ok(self, client):
        r = client.get("/api/v1/institutions", params={"lang": "vi", "visa": "E-9"})
        assert r.status_code == 200
        assert len(r.json()["results"]) == 6

    def test_invalid_visa_is_rejected(self, client):
        """F1 과 같은 규칙 — 열거값 외는 422 (자유 입력이 없다)."""
        assert client.get("/api/v1/institutions", params={"visa": "X-1"}).status_code == 422

    def test_visa_is_optional(self, client):
        """프로필 없이도 기관 단위 정보는 볼 수 있어야 한다."""
        r = client.get("/api/v1/institutions", params={"lang": "en"})
        assert r.status_code == 200
        assert len(r.json()["results"]) == 6

    def test_ranking_policy_is_public(self, client):
        r = client.get("/api/v1/institutions/ranking-policy")
        assert r.status_code == 200
        assert "fit_score" in r.json()["formula"]

    def test_every_official_card_carries_a_source_url(self, client):
        r = client.get("/api/v1/institutions", params={"visa": "E-9"}).json()
        for card in r["results"]:
            if card["status"] == "official":
                assert card["evidence"] and card["evidence"][0]["url"].startswith("http")

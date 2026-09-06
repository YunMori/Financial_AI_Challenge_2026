"""F3 서류 체크리스트 (planner §9.2, §12).

이 파일이 지키는 주장은 셋이다.

1. **금융위 목록과 법무부 목록을 섞지 않는다.** 섞이면 이용자가 재학증명서를
   들고 은행에 간다.
2. **근거 없는 조합을 만들지 않는다.** 확인하지 못한 거래목적은 빈 절로 남고,
   빈 절을 지우지 않는다 — "확인하지 못했다"는 것 자체가 답이다.
3. **안전 문구가 요청 언어로 나온다.** "예시일 뿐이고 은행마다 다르다"는 경고가
   한국어로만 보이면 전달되지 않는다.

PDF **렌더**는 여기서 검증하지 않는다. WeasyPrint 는 로컬에서 import 자체가
실패하므로 컨테이너에서만 확인할 수 있다:
`docker build -f apps/api/scripts/typeset/Dockerfile.checklist -t kb-checklist .`
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.docs.checklist import build_checklist, load_catalog
from app.docs.pdf_render import render_html
from app.main import app
from app.schemas.common import EvidenceStatus, Lang, Tier


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _sections(doc):
    return {s.key: s for s in doc.sections}


class TestSectionSeparation:
    """★ 두 목록은 성격이 다르다 — 한 표에 놓지 않는다."""

    def test_bank_and_immigration_documents_are_separate_sections(self):
        s = _sections(build_checklist(Lang.KO, "D-2", "salary"))
        assert "account_opening" in s and "foreign_registration" in s
        assert s["account_opening"].items != s["foreign_registration"].items

    def test_immigration_section_says_it_is_not_a_bank_requirement(self):
        s = _sections(build_checklist(Lang.KO, "D-2", "salary"))
        assert "계좌개설" in s["foreign_registration"].caveat

    def test_enrollment_cert_never_lands_in_the_bank_section(self):
        """D-2 의 재학증명서는 출입국 서류다. 은행 절에 새어 들어가면 안 된다."""
        s = _sections(build_checklist(Lang.KO, "D-2", "tuition"))
        bank_codes = {i.code for i in s["account_opening"].items}
        assert "ENROLLMENT_CERT" not in bank_codes


class TestUnknownIsNotFilled:
    """★ 근거 없는 조합을 지어내지 않는다."""

    @pytest.mark.parametrize("purpose", ["tuition", "living", "remittance"])
    def test_purposes_absent_from_the_official_table_stay_empty(self, purpose):
        s = _sections(build_checklist(Lang.KO, "E-9", purpose))
        section = s["account_opening"]
        assert section.status is EvidenceStatus.UNKNOWN
        assert section.items == []

    def test_empty_section_is_kept_not_dropped(self):
        """빈 절을 지우면 '확인하지 못했다'는 사실이 화면에서 사라진다."""
        s = _sections(build_checklist(Lang.EN, "E-9", "living"))
        assert "account_opening" in s

    def test_confirmed_purpose_carries_evidence(self):
        s = _sections(build_checklist(Lang.KO, "E-9", "salary"))
        section = s["account_opening"]
        assert section.status is EvidenceStatus.OFFICIAL
        assert section.evidence[0].doc_id == "FSC-ACCOUNT-OPENING-PRACTICE"

    def test_official_without_evidence_raises(self):
        """매트릭스 로더와 같은 불변식이 카탈로그에도 적용된다."""
        from app.docs.checklist import _section

        with pytest.raises(ValueError, match="evidence"):
            _section("base", {"status": "official", "doc_codes": [], "evidence": []}, Lang.KO)

    def test_shipped_catalog_satisfies_the_invariant(self):
        catalog = load_catalog()
        for purpose, block in catalog.account_opening["by_purpose"].items():
            if block["status"] == "official":
                assert block.get("evidence"), purpose


class TestLocalization:
    """★ 안전 문구가 요청 언어로 나온다."""

    @pytest.mark.parametrize("lang", [Lang.EN, Lang.VI])
    def test_caveat_is_translated_not_korean(self, lang):
        s = _sections(build_checklist(lang, "E-9", "salary"))
        caveat = s["account_opening"].caveat
        assert caveat
        # 한글이 남아 있으면 번역이 폴백된 것이다.
        assert not any("가" <= ch <= "힯" for ch in caveat), caveat

    @pytest.mark.parametrize("lang", [Lang.EN, Lang.VI])
    def test_notes_are_translated(self, lang):
        s = _sections(build_checklist(lang, "E-9", "salary"))
        for key in ("base", "account_opening"):
            notes = s[key].notes
            assert not any("가" <= ch <= "힯" for ch in notes), (key, notes)

    def test_document_labels_come_from_the_glossary(self):
        s = _sections(build_checklist(Lang.VI, "E-9", "salary"))
        labels = {i.code: i.label for i in s["account_opening"].items}
        assert labels["EMPLOYMENT_CERT"] == "Giấy xác nhận công tác"

    def test_korean_original_is_always_carried(self):
        """창구에서 보여주는 용도라 한국어 원어가 반드시 함께 간다."""
        s = _sections(build_checklist(Lang.VI, "E-9", "salary"))
        assert all(i.label_ko for i in s["account_opening"].items)


class TestFilenameAndTier:
    def test_filename_follows_the_convention(self):
        doc = build_checklist(Lang.VI, "E-9", "salary", today=date(2026, 8, 20))
        assert doc.filename == "KBuddy_checklist_E-9_vi_20260820.pdf"

    def test_filename_without_visa(self):
        doc = build_checklist(Lang.KO, today=date(2026, 8, 20))
        assert doc.filename == "KBuddy_checklist_ALL_ko_20260820.pdf"

    def test_tier_is_b_when_documents_exist(self):
        assert build_checklist(Lang.KO, "E-9", "salary").disclaimer_tier is Tier.B


class TestHtmlRender:
    """PDF 의 소스이자 §12.3 폴백 산출물. WeasyPrint 없이 검증할 수 있다."""

    def test_two_column_layout_for_non_korean(self):
        html = render_html(build_checklist(Lang.VI, "E-9", "salary"))
        assert "Giấy xác nhận công tác" in html and "재직증명서" in html

    def test_korean_column_omitted_for_korean_users(self):
        html = render_html(build_checklist(Lang.KO, "E-9", "salary"))
        # ko 이용자에게 한국어를 두 번 보여줄 이유가 없다.
        assert html.count("재직증명서") == 1

    def test_include_ko_false_drops_the_second_column(self):
        html = render_html(build_checklist(Lang.VI, "E-9", "salary"), include_ko=False)
        assert "재직증명서" not in html

    def test_checkbox_per_item(self):
        html = render_html(build_checklist(Lang.KO, "E-9", "salary"))
        assert html.count("☐") >= 5

    def test_mandatory_notices_are_printed(self):
        """planner §11.2 의 상시 고지가 인쇄물에도 따라간다."""
        html = render_html(build_checklist(Lang.VI, "E-9", "salary"))
        assert "quyết định cuối cùng" in html  # 최종책임
        assert "mật khẩu" in html  # 안티피싱

    def test_source_urls_are_printed(self):
        html = render_html(build_checklist(Lang.KO, "E-9", "salary"))
        assert "https://www.fsc.go.kr" in html

    def test_html_is_escaped(self):
        """서류명이 사전에서 오므로 주입 여지는 낮지만 이스케이프를 고정한다."""
        html = render_html(build_checklist(Lang.KO, "E-9", "salary"))
        assert "<script" not in html


class TestEndpoints:
    def test_preview_returns_json(self, client):
        r = client.post("/api/v1/checklist/preview",
                        json={"lang": "vi", "visa": "E-9", "purpose": "salary"})
        assert r.status_code == 200
        assert len(r.json()["sections"]) == 3

    def test_unknown_institution_is_rejected(self, client):
        r = client.post("/api/v1/checklist/preview",
                        json={"lang": "ko", "inst_code": "BANK_NOPE"})
        assert r.status_code == 422

    def test_known_institution_is_named_in_the_request_language(self, client):
        r = client.post("/api/v1/checklist/preview",
                        json={"lang": "vi", "inst_code": "BANK_SHINHAN"})
        assert r.json()["institution"] == "Ngân hàng Shinhan"

    def test_unsupported_language_is_rejected(self, client):
        assert client.post("/api/v1/checklist/preview",
                           json={"lang": "de"}).status_code == 422

    def test_pdf_route_degrades_to_503_without_weasyprint(self, client):
        """★ WeasyPrint 는 `OSError` 로 죽는다 — `ImportError` 만 잡으면 500 이다.

        로컬(Windows·macOS)에서는 503 이 정상이다. **컨테이너에서 503 이 나오면
        Dockerfile 의 조판 의존성이 깨진 것**이다.
        """
        r = client.post("/api/v1/checklist",
                        json={"lang": "vi", "visa": "E-9", "purpose": "salary"})
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            assert r.headers["content-type"] == "application/pdf"
            assert "KBuddy_checklist_E-9_vi_" in r.headers["content-disposition"]
            assert r.headers["cache-control"] == "no-store"

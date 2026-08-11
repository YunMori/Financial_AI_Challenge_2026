"""검색 계층 테스트.

인덱스가 없어도 도는 단위 테스트와, 인덱스가 있을 때만 도는 통합 테스트를
나눈다. CI 에서 인덱스를 빌드하지 않아도 로직 회귀는 잡히게 하기 위해서다.
"""

from datetime import date

import pytest

from app.config import get_settings
from app.rag.glossary import load_glossary
from app.rag.normalize import QueryNormalizer
from app.rag.retrieve import Candidate


class TestGlossary:
    def test_loads(self):
        g = load_glossary()
        assert len(g) >= 30
        assert g.get("LIMITED_ACCOUNT").ko == "한도제한계좌"

    def test_reverse_lookup_english(self):
        assert "한도제한계좌" in load_glossary().reverse_lookup("Limited-Purpose Account")

    def test_reverse_lookup_vietnamese(self):
        assert "외국인등록증" in load_glossary().reverse_lookup(
            "Tôi cần Thẻ đăng ký người nước ngoài"
        )

    def test_longest_match_wins(self):
        """'모바일 외국인등록증'이 '외국인등록증'으로 잘리면 안 된다."""
        hits = load_glossary().reverse_lookup("모바일 외국인등록증 발급 방법")
        assert "모바일 외국인등록증" in hits
        assert "외국인등록증" not in hits

    def test_missing_translation_falls_back_to_korean(self):
        """ORIS 는 베트남어 표기를 확보하지 못했다 — 원어로 폴백한다."""
        assert load_glossary().get("ORIS").label("vi") == "해외송금 통합관리시스템"

    def test_prompt_block_is_substantial(self):
        """시스템 프롬프트에 주입되며 sonnet-5 캐시 하한에도 기여한다."""
        assert len(load_glossary().for_prompt("vi")) > 500


class TestNormalizer:
    def test_korean_passthrough(self):
        nq = QueryNormalizer().normalize("한도제한계좌 해제 서류", lang="ko")
        assert nq.via == "passthrough"
        assert "한도제한계좌" in nq.glossary_hits

    def test_english_uses_glossary_without_llm(self):
        """사전 히트가 2개 이상이면 LLM 을 건너뛴다 (planner §6.2)."""
        nq = QueryNormalizer().normalize(
            "Limited-Purpose Account and Certificate of Employment", lang="en"
        )
        assert nq.via == "glossary"
        assert "한도제한계좌" in nq.ko and "재직증명서" in nq.ko

    def test_visa_from_query_beats_session(self):
        """이용자가 방금 말한 자격이 세션 값보다 정확한 의도다."""
        nq = QueryNormalizer().normalize("D-2 유학생인데요", lang="ko", visa="E-9")
        assert nq.visa == "D2"

    def test_visa_appended_to_search_terms(self):
        nq = QueryNormalizer().normalize("계좌 개설 방법", lang="ko", visa="E-9")
        assert nq.ko.startswith("E-9")

    def test_visa_not_duplicated(self):
        nq = QueryNormalizer().normalize("E-9 계좌 개설", lang="ko", visa="E-9")
        assert nq.ko.upper().count("E-9") == 1

    def test_no_translator_does_not_crash(self):
        """API 키가 없어도 파이프라인이 멈추지 않아야 한다."""
        nq = QueryNormalizer(translator=None).normalize("Xin chào", lang="vi")
        assert not nq.is_empty

    def test_translator_failure_falls_back(self):
        class Boom:
            def to_search_terms(self, query, lang):
                raise RuntimeError("api down")

        nq = QueryNormalizer(translator=Boom()).normalize(
            "Limited-Purpose Account", lang="en"
        )
        assert nq.via == "passthrough" and "한도제한계좌" in nq.ko

    def test_cache_returns_same_object(self):
        n = QueryNormalizer()
        a = n.normalize("한도제한계좌", lang="ko")
        assert n.normalize("한도제한계좌", lang="ko") is a


class TestCandidate:
    def _cand(self, **meta) -> Candidate:
        return Candidate(chunk_id="X#0", text="t", meta=meta)

    def test_visa_scope_parsing(self):
        assert self._cand(visa_scope="|E-9|D-2|").visa_scope == ["E-9", "D-2"]

    def test_missing_verified_at_counts_as_stale(self):
        """모르는 것을 최신으로 취급하면 시점 경고의 의미가 없다."""
        assert self._cand(verified_at="").is_stale(date(2026, 8, 12), 90)

    def test_malformed_verified_at_counts_as_stale(self):
        assert self._cand(verified_at="2026/08/12").is_stale(date(2026, 8, 12), 90)

    def test_recent_is_not_stale(self):
        assert not self._cand(verified_at="2026-08-01").is_stale(date(2026, 8, 12), 90)

    def test_old_is_stale(self):
        assert self._cand(verified_at="2026-01-01").is_stale(date(2026, 8, 12), 90)


class TestThresholdConfig:
    def test_threshold_matches_measured_distribution(self):
        """planner 원안의 0.42 는 e5 척도에서 폴백을 전혀 걸지 못한다.

        실측(2026-08-12): 코퍼스 안 최소 0.8494 / 밖 최대 0.8293.
        값을 되돌리면 이 테스트가 막는다.
        """
        s = get_settings()
        assert 0.83 <= s.threshold_top1 <= 0.86, (
            f"threshold_top1={s.threshold_top1} 은 실측 분포 밖입니다. "
            "임베딩 모델을 바꿨다면 `python -m app.rag.cli --calibrate` 로 다시 재세요."
        )

    def test_margin_disabled(self):
        """RRF margin 은 신뢰도와 무관하다 — 코퍼스 밖 질의가 더 높게 나왔다."""
        assert get_settings().threshold_margin == 0.0


@pytest.mark.integration
class TestHybridSearch:
    """인덱스가 있을 때만 도는 통합 테스트."""

    @pytest.fixture(autouse=True)
    def _require_index(self):
        s = get_settings()
        if not s.chroma_path.exists() or not s.bm25_index_path.exists():
            pytest.skip("인덱스 없음 — corpus/scripts/04_index.py 를 실행하세요")

    def test_in_corpus_query_retrieves_relevant_doc(self):
        from app.rag.retrieve import get_retriever

        res = get_retriever().search("한도제한계좌 이체 한도")
        assert res.candidates
        assert any("LIMIT" in c.chunk_id for c in res.candidates)

    def test_out_of_corpus_query_is_below_threshold(self):
        from app.rag.retrieve import get_retriever

        res = get_retriever().search("제주도 맛집 추천")
        assert res.top1_dense < get_settings().threshold_top1

    def test_visa_table_chunk_covers_multiple_statuses(self):
        """체류자격 제출서류 표가 쪼개졌다면 이 테스트가 잡는다."""
        from app.rag.retrieve import get_retriever

        res = get_retriever().search("체류자격별 외국인등록 제출서류")
        table = next((c for c in res.candidates if "HIKOREA-ARC-DOCS" in c.chunk_id), None)
        assert table is not None, "제출서류 표가 검색되지 않았습니다"
        assert sum(code in table.text for code in ("D-2", "E-7", "E-9", "F-6")) >= 4

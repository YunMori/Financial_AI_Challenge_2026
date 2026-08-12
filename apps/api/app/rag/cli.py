"""검색 단독 확인용 CLI.

챗 API 가 붙기 전에 검색 품질을 분리해서 볼 수 있어야 디버깅이 갈린다.
"검색이 못 찾은 것"과 "모델이 근거를 못 쓴 것"은 완전히 다른 문제인데,
엔드투엔드로만 보면 구분되지 않는다.

    python -m app.rag.cli "E-9 한도제한계좌 해제 서류"
    python -m app.rag.cli --visa D-2 --lang vi "Giấy tờ mở tài khoản"
    python -m app.rag.cli --calibrate      # 임계값 보정용 점수 분포 출력
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from app.config import get_settings
from app.rag.normalize import QueryNormalizer
from app.rag.retrieve import get_retriever

# 임계값 보정용. 코퍼스에 근거가 **있는** 질의와 **없는** 질의를 나눠 두고
# 두 분포가 갈리는 지점을 본다. 골든셋이 생기기 전의 잠정 도구다.
IN_CORPUS_QUERIES = [
    "한도제한계좌 이체 한도가 왜 100만원인가요",
    "한도제한계좌 창구 거래 한도",
    "D-2 유학생 외국인등록 제출서류",
    "E-9 비전문취업 외국인등록 서류",
    "모바일 외국인등록증으로 계좌 개설되는 은행",
    "모바일 외국인등록증 발급 방법",
    "외국인등록 대상과 신청 시기",
    "금융거래 목적 확인이 뭔가요",
]
OUT_OF_CORPUS_QUERIES = [
    "주택청약 종합저축 금리",
    "코스피 지수 전망",
    "자동차 보험료 할인 특약",
    "국민연금 수령 나이",
    "제주도 맛집 추천",
    "파이썬 리스트 정렬 방법",
    "전세자금대출 한도",     # 코퍼스에 '한도' 문서가 늘면 함께 올라가는 함정
    "삼성전자 주가",
]

# 언어별 보정. 다국어 임베딩은 **같은 언어 쌍을 교차 언어 쌍보다 체계적으로
# 높게** 주므로 한국어로 잡은 값 하나를 전 언어에 쓰면 비한국어가 전부 폴백된다.
# ko 만 고치고 en/vi 를 두면 그 순간 언어별 값이 서로 어긋난다.
QUERIES_BY_LANG: dict[str, tuple[list[str], list[str]]] = {
    "ko": (IN_CORPUS_QUERIES, OUT_OF_CORPUS_QUERIES),
    "en": (
        [
            "What is a limited-purpose account daily transfer limit",
            "Documents to register as a foreign resident with a D-2 visa",
            "Which banks accept the mobile alien registration card",
            "Why does the bank ask my purpose of financial transaction",
        ],
        [
            "How is the KOSPI index doing",
            "Best restaurants in Jeju island",
            "How to sort a list in Python",
            "Jeonse loan limit for tenants",
        ],
    ),
    "vi": (
        [
            "Hạn mức chuyển khoản hàng ngày của tài khoản hạn chế",
            "Giấy tờ đăng ký người nước ngoài cho visa D-2",
            "Ngân hàng nào chấp nhận thẻ đăng ký người nước ngoài di động",
            "Tại sao ngân hàng hỏi mục đích giao dịch tài chính",
        ],
        [
            "Chỉ số KOSPI hôm nay",
            "Nhà hàng ngon ở đảo Jeju",
            "Cách sắp xếp danh sách trong Python",
            "Hạn mức vay mua nhà jeonse",
        ],
    ),
}


def run_query(query: str, lang: str, visa: str | None, top_n: int, verbose: bool) -> None:
    normalizer = QueryNormalizer()
    retriever = get_retriever()
    s = get_settings()

    t0 = time.perf_counter()
    nq = normalizer.normalize(query, lang=lang, visa=visa)
    t_norm = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    result = retriever.search(nq.ko, visa=nq.visa, top_n=top_n)
    t_search = (time.perf_counter() - t0) * 1000

    print(f"질의   {query!r}  (lang={lang})")
    print(f"정규화 {nq.ko!r}  via={nq.via} visa={nq.visa} "
          f"사전히트={nq.glossary_hits}  {t_norm:.1f}ms")
    print(f"검색   후보 {result.n_before_filter}건 → {len(result.candidates)}건  "
          f"top1_dense={result.top1_dense:.3f} margin={result.margin:.5f}  {t_search:.0f}ms")
    if result.visa_filter_relaxed:
        print("       ⚠ 체류자격 필터 해제됨 (후보 부족)")

    below = result.top1_dense < s.threshold_top1
    thin = result.margin < s.threshold_margin
    if below or thin:
        reasons = []
        if below:
            reasons.append(f"top1 {result.top1_dense:.3f} < {s.threshold_top1}")
        if thin:
            reasons.append(f"margin {result.margin:.5f} < {s.threshold_margin}")
        print(f"       → 현재 임계값이면 폴백 ({', '.join(reasons)})")

    today = date.today()
    print()
    for i, c in enumerate(result.candidates, 1):
        flags = []
        if c.is_stale(today, s.stale_days):
            flags.append("STALE")
        if c.meta.get("is_table"):
            flags.append("표")
        print(f"[근거 {i}] rrf={c.rrf:.5f}  "
              f"dense={c.dense_score if c.dense_score is None else round(c.dense_score, 3)}"
              f"(#{c.dense_rank})  "
              f"bm25={c.lexical_score if c.lexical_score is None else round(c.lexical_score, 2)}"
              f"(#{c.lexical_rank})  {' '.join(flags)}")
        print(f"          {c.chunk_id}")
        print(f"          {c.meta['publisher']} 「{c.meta['title'][:40]}」 "
              f"발행 {c.meta.get('published_at') or '미상'} / 확인 {c.meta.get('verified_at') or '미기재'}")
        body = c.text.split("\n", 1)[-1].replace("\n", " ")
        print(f"          {body[:110]}…")
        if verbose:
            print(f"          ─ 전문 ─\n{c.text}\n")
        print()


def calibrate() -> None:
    """코퍼스 내/외 질의의 점수 분포를 비교한다.

    설정의 임계값은 다른 척도를 가정한 값이라 그대로 쓰면 폴백이 전혀
    걸리지 않거나 전부 걸린다. 골든셋이 생기기 전까지의 잠정 근거로 쓴다.
    """
    normalizer = QueryNormalizer()
    retriever = get_retriever()
    settings = get_settings()

    def measure(queries: list[str], lang: str) -> list[tuple[str, float, float]]:
        rows = []
        for q in queries:
            nq = normalizer.normalize(q, lang=lang)
            r = retriever.search(nq.ko, visa=nq.visa)
            rows.append((q, r.top1_dense, r.margin))
        return rows

    proposed: dict[str, float] = {}
    for lang, (in_qs, out_qs) in QUERIES_BY_LANG.items():
        inside, outside = measure(in_qs, lang), measure(out_qs, lang)
        print(f"\n═══ {lang} ═══   {'top1_dense':>11} {'margin':>10}")
        for label, rows in (("코퍼스 안", inside), ("코퍼스 밖", outside)):
            print(f"── {label} ──")
            for q, top1, margin in rows:
                print(f"{q[:44]:46} {top1:>11.4f} {margin:>10.5f}")

        lo = min(t for _, t, _ in inside)
        hi = max(t for _, t, _ in outside)
        gap = lo - hi
        print(f"  안 최소 {lo:.4f} / 밖 최대 {hi:.4f} / 분리 폭 {gap:+.4f}")
        if gap > 0:
            proposed[lang] = round((lo + hi) / 2, 3)
            print(f"  → 임계값 후보 {proposed[lang]:.3f}")
        else:
            print("  → 두 분포가 겹칩니다. top1 단독으로는 폴백 판정을 할 수 없습니다.")
            print("    리랭킹 도입을 앞당겨야 합니다 (ADR-001).")

    print("\n─── 현재 설정과 비교 ───")
    stale = []
    for lang, value in proposed.items():
        now = settings.threshold_for(lang)
        mark = "  " if abs(now - value) < 0.006 else "★ "
        if mark == "★ ":
            stale.append(f"{lang}:{value:.3f}")
        print(f"{mark}{lang}  현재 {now:.3f}  →  실측 {value:.3f}")
    if stale:
        print("\n★ 표시된 언어는 실측과 어긋납니다. .env 또는 config.py 를 고치세요:")
        print(f"    THRESHOLD_TOP1_BY_LANG={','.join(stale)}")
        print("  ※ **한 언어만 고치지 마세요.** 언어별 값이 서로 어긋나면")
        print("    특정 언어만 조용히 폴백되거나 조용히 통과합니다.")
    print("\n※ 잠정값입니다. 골든셋(함정 문항 포함)이 생기면 다시 잡아야 합니다.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="검색할 질의")
    ap.add_argument("--lang", default="ko", choices=["ko", "en", "vi"])
    ap.add_argument("--visa", help="체류자격 (예: E-9)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("-v", "--verbose", action="store_true", help="청크 전문 출력")
    ap.add_argument("--calibrate", action="store_true", help="임계값 보정용 분포 출력")
    args = ap.parse_args()

    if args.calibrate:
        calibrate()
        return 0
    if not args.query:
        ap.error("질의를 입력하거나 --calibrate 를 쓰세요")
    run_query(args.query, args.lang, args.visa, args.top, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())

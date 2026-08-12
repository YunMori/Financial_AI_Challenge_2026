"""② 정제 — 원문에서 본문을 뽑아 front-matter 가 붙은 마크다운으로 만든다.

HTML 은 trafilatura, PDF 는 pdfplumber 를 쓴다. 파서 선택은 확장자가 아니라
01_fetch 가 매직 바이트로 판별해 둔 결과를 따른다.

**실질 변경 판정이 여기 있다.** 01_fetch 의 바이트 해시는 출처 기록용이고,
정부 포털 HTML 은 조회수·세션토큰 때문에 매 요청 바이트가 달라진다.
그래서 변경 감지는 **추출 텍스트의 해시**로 한다 — 이것이 "근거가 조용히
바뀌었는가"라는 질문에 실제로 답하는 값이다(planner §8.4).

정제 품질보다 **메타데이터 정확도가 우선**이다. 본문에 잡음이 조금 남는 것은
검색으로 흡수되지만, published_at 이 틀리면 시점 경고 기능 전체가 무의미해진다.

사용법
------
    python corpus/scripts/02_clean.py
    python corpus/scripts/02_clean.py --only FSC-2024-0502-LIMIT-GUIDE
    python corpus/scripts/02_clean.py --verify   # 쓰지 않고 변경 여부만 판정
"""

from __future__ import annotations

import argparse
import re
import sys
import zlib
from itertools import combinations

from urllib.parse import urlparse

import pdfplumber
import trafilatura
import yaml

from _common import (
    PROCESSED_DIR,
    Source,
    load_manifest,
    load_sources,
    save_manifest,
    sha256_bytes,
    tidy,
)

# 본문이 이보다 짧으면 추출 실패로 본다. 정부 안내 페이지는 최소 수백 자다.
MIN_BODY_CHARS = 400
# 앞에 나온 블록이 이 줄 수 이상 그대로 되풀이되면 중복으로 본다.
DUP_RUN_MIN = 3


def decode_html(raw: bytes) -> str:
    """인코딩을 판별해 문자열로 만든다.

    국내 기관 사이트 중에는 아직 **EUC-KR/CP949** 로 서비스하는 곳이 있다
    (은행연합회 소비자포털). UTF-8 로 못박고 `errors="replace"` 를 쓰면
    예외 없이 조용히 통과하면서 본문이 전부 깨진 글자가 된다 — 색인까지
    들어가고 나서야 발견된다. 실제로 그렇게 한 번 들어갔다.

    **엄격 디코딩으로는 고를 수 없다.** 은행연합회 소비자포털은 한 파일 안에
    템플릿이 UTF-8, 본문이 CP949 인 혼합 인코딩이라 UTF-8 도 CP949 도 예외를
    낸다. meta 태그도 `charset="UTF-8"` 과 `charset=euc-kr` 을 둘 다 달고 있어
    선언을 믿을 수 없다.

    그래서 후보마다 `errors="replace"` 로 디코딩해 **깨진 글자가 가장 적은 것**을
    고른다. 온전히 디코딩되는 인코딩이 있으면 깨진 글자가 0이라 자연히 이긴다.
    CP949 는 EUC-KR 의 상위집합이라 둘을 따로 볼 필요가 없다.
    """
    head = raw[:4096].decode("ascii", errors="ignore").lower()
    seen = re.findall(r'charset=["\']?\s*([\w-]+)', head)
    best, fewest = "", None
    for enc in [*seen, "utf-8", "cp949"]:
        try:
            text = raw.decode(enc, errors="replace")
        except LookupError:
            continue
        bad = text.count("�")
        if fewest is None or bad < fewest:
            best, fewest = text, bad
            if bad == 0:
                break
    return best


def extract_html(path) -> str:
    """trafilatura 로 본문만 뽑는다 (머리말·네비게이션·푸터 제거)."""
    html = decode_html(path.read_bytes())
    text = trafilatura.extract(
        html,
        include_tables=True,      # 요건 비교표가 핵심 근거인 경우가 많다
        include_comments=False,
        include_formatting=False,
        favor_recall=True,        # 정부 페이지는 마크업이 단조로워 recall 을 높인다
    )
    return tidy(text or "")


def extract_pdf(path) -> str:
    """pdfplumber 로 페이지별 텍스트 + 표를 뽑는다.

    표는 마크다운 표로 변환해 통째로 남긴다. 03_chunk 가 표를 쪼개지 않도록
    하려면 여기서 표 경계가 살아 있어야 한다.
    """
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            if body := (page.extract_text() or "").strip():
                parts.append(body)
            for table in page.extract_tables() or []:
                if md := table_to_markdown(table):
                    parts.append(md)
            if i >= 40:  # 보도자료가 40쪽을 넘는 일은 없다 — 방어
                parts.append(f"\n<!-- {i}쪽 이후 생략 -->")
                break
    return tidy("\n\n".join(parts))


# ── HWP (한글) ───────────────────────────────────────────────────────
# 국내 정부 보도자료는 첨부를 HWP 로 올린다. PDF 본을 같이 올리는 경우에도
# **PDF 쪽 표가 깨져 있는 일이 있다** — pdfplumber 가 82205 첨부에서 '개선방안'
# 열을 통째로 놓쳤고, 그 결과 상향 *전* 값만 표에 남았다(dev-log 2026-08-12).
# HWP 는 표의 행·열 구조를 레코드로 갖고 있어 오히려 복원이 정확하다.
#
# HWP 5.0 = OLE 복합문서. BodyText/SectionN 이 raw deflate 로 압축돼 있고,
# 그 안이 (태그, 레벨, 크기) 헤더가 붙은 레코드 열이다.
HWPTAG_BEGIN = 0x010
TAG_PARA_TEXT = HWPTAG_BEGIN + 51    # 67 — 문단 텍스트 (UTF-16LE)
TAG_LIST_HEADER = HWPTAG_BEGIN + 56  # 72 — 셀 하나의 시작
TAG_TABLE = HWPTAG_BEGIN + 61        # 77 — 행/열 수와 행별 셀 수

# 8 WCHAR(16바이트)를 차지하는 제어문자. 1바이트로 세면 이후가 전부 밀린다.
HWP_WIDE_CTRL = frozenset(
    {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
)


def _hwp_records(data: bytes):
    """(태그, 레벨, 페이로드). **레벨이 표의 경계를 알려준다** — 셀 안 문단은
    표 레코드보다 깊고, 표 뒤 본문은 얕다."""
    i, n = 0, len(data)
    while i + 4 <= n:
        head = int.from_bytes(data[i:i + 4], "little")
        i += 4
        tag, level, size = head & 0x3FF, (head >> 10) & 0x3FF, (head >> 20) & 0xFFF
        if size == 0xFFF:  # 확장 크기
            size = int.from_bytes(data[i:i + 4], "little")
            i += 4
        yield tag, level, data[i:i + size]
        i += size


def _hwp_para(payload: bytes) -> str:
    out, j = [], 0
    while j + 2 <= len(payload):
        c = int.from_bytes(payload[j:j + 2], "little")
        if c in HWP_WIDE_CTRL:
            j += 16
            continue
        if c in (10, 13):
            out.append("\n")
        elif c >= 32:
            out.append(chr(c))
        j += 2
    return "".join(out)


def _hwp_table(payload: bytes, recs: list, i: int, level: int) -> tuple[str, int]:
    """표를 격자에 그대로 놓는다.

    각 셀의 LIST_HEADER 가 **(열, 행, 열병합, 행병합)** 을 갖고 있으므로
    셀을 순서대로 이어붙이는 대신 좌표에 배치한다. 병합이 있는 표를 순서대로
    붙이면 값이 한 칸씩 밀려 **다른 행의 숫자를 읽게 된다** — 한도 표처럼
    "창구 300만원 / ATM 100만원"을 구분해야 하는 문서에서 치명적이다.
    """
    n_rows = int.from_bytes(payload[4:6], "little")
    n_cols = int.from_bytes(payload[6:8], "little")
    if not (0 < n_rows <= 200 and 0 < n_cols <= 40) or len(payload) < 18 + 2 * n_rows:
        return "", i
    want = sum(
        int.from_bytes(payload[18 + 2 * r:20 + 2 * r], "little") for r in range(n_rows)
    )
    if not want:
        return "", i

    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    seen, pos, cur = 0, None, []

    def place() -> None:
        if pos is None:
            return
        col, row = pos
        if row < n_rows and col < n_cols:
            grid[row][col] = " ".join(cur).strip().replace("|", "／")

    while i < len(recs) and seen <= want:
        tag, lvl, p = recs[i]
        # 셀의 LIST_HEADER 는 표 레코드와 **같은 레벨**이다. `>` 로 두면 셀을
        # 하나도 못 찾고 루프가 문서 끝까지 달려 본문을 통째로 삼킨다.
        if tag == TAG_LIST_HEADER and lvl >= level:
            if seen == want:
                break
            place()
            cur = []
            pos = (int.from_bytes(p[8:10], "little"), int.from_bytes(p[10:12], "little"))
            seen += 1
        elif tag == TAG_TABLE and seen:
            break  # 중첩 표 — 바깥 루프가 다시 처리한다
        elif tag == TAG_PARA_TEXT:
            # 표를 빠져나온 문단이 마지막 셀에 딸려 들어가는 것을 막는다.
            # 셀 안 문단은 표보다 깊고, 표 뒤 본문은 얕다.
            if lvl <= level:
                break
            if seen and (t := _hwp_para(p).strip()):
                cur.append(t)
        i += 1
    place()

    rows = [r for r in grid if any(r)]
    if len(rows) < 2:
        return "\n".join(" ".join(c for c in r if c) for r in rows), i
    head, *body = rows
    md = ["| " + " | ".join(head) + " |", "|" + "---|" * n_cols]
    md += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(md), i


def extract_hwp(path) -> str:
    """HWP 5.0 본문 + 표를 마크다운으로 뽑는다."""
    import olefile

    ole = olefile.OleFileIO(str(path))
    try:
        flags = ole.openstream("FileHeader").read()[36]
        if flags & 2:
            raise ValueError("암호화된 HWP 는 읽을 수 없습니다")
        compressed = bool(flags & 1)
        parts: list[str] = []
        for entry in sorted(e for e in ole.listdir() if e[0] == "BodyText"):
            blob = ole.openstream("/".join(entry)).read()
            recs = list(_hwp_records(zlib.decompress(blob, -15) if compressed else blob))
            i, lines = 0, []
            while i < len(recs):
                tag, level, payload = recs[i]
                if tag == TAG_TABLE:
                    md, i = _hwp_table(payload, recs, i + 1, level)
                    if md:
                        lines.append(md)
                    continue
                if tag == TAG_PARA_TEXT:
                    if t := _hwp_para(payload).strip():
                        lines.append(t)
                i += 1
            parts.append("\n".join(lines))
    finally:
        ole.close()
    return tidy("\n\n".join(parts))


def empty_columns(text: str) -> list[tuple[int, int]]:
    """마크다운 표에서 **통째로 빈 열**을 찾는다.

    표 추출이 실패하는 방식은 "표가 안 나온다"가 아니라 **한 열이 조용히
    비는 것**이다. 82205 첨부 PDF 에서 '개선방안' 열이 통째로 사라져
    상향 *전* 값(창구 100만원)만 표에 남았다 — 그대로 두면 검색이 옛 숫자를
    현재 값으로 인용한다. 조용히 틀리는 것이 안 나오는 것보다 훨씬 나쁘다.

    반환값은 (표 시작 줄 번호, 빈 열 번호) 목록이다.
    """
    found: list[tuple[int, int]] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        start = i
        rows = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            if not all(set(c) <= {"-", ":"} and c for c in cells):
                rows.append(cells)
            i += 1
        if len(rows) < 3:  # 헤더 + 본문 2줄 미만이면 판단하지 않는다
            continue
        # 첫 열은 건너뛴다. 정부 문서의 표는 행 이름을 세로로 병합하는 일이
        # 흔해서(담당 부서·기관명) 첫 열이 비는 것은 정상이다. 이 조건 없이는
        # 경보 5건 중 4건이 오탐이었고, **늘 울리는 경보는 곧 무시된다.**
        for col in range(1, max(len(r) for r in rows)):
            body = [r[col] for r in rows[1:] if col < len(r)]
            if body and not any(body):
                found.append((start + 1, col + 1))
    return found


SHINGLE = 12       # 문자 n-gram 길이
NEAR_DUP = 0.35    # 이 이상 겹치면 사실상 같은 문서로 본다.
# 문자 n-gram 은 **형식이 다른 같은 문서를 과소평가한다** — 같은 보도자료의
# HWP 판과 PDF 판이 24% 로만 나왔다(줄바꿈·표 서식이 달라서). 그러니 이 경보가
# 조용하다고 중복이 없는 것은 아니다. 새 문서를 넣을 때 출처 URL 의 게시물
# 번호가 겹치는지 사람이 함께 본다.


def _shingles(text: str) -> set[int]:
    flat = "".join(text.split())
    return {hash(flat[i:i + SHINGLE]) for i in range(0, max(0, len(flat) - SHINGLE), 3)}


def near_duplicates(docs: dict[str, str]) -> list[tuple[str, str, float]]:
    """**문서 사이**의 중복을 잡는다 (`dedupe_repeats` 는 문서 안쪽만 본다).

    같은 보도자료가 HTML·PDF·HWP·교육란·정책브리핑으로 다섯 번 들어오는 일이
    실제로 있었다. 검색 관점에서 이건 단순 낭비가 아니라 **측정 붕괴**다 —
    상위 5건이 한 문서의 사본으로 채워지면 다른 근거가 밀려나고, Recall@5 가
    실제보다 좋아 보인다(같은 문서를 다섯 번 맞히므로).

    겹침은 자카드가 아니라 **작은 쪽 기준 포함률**로 잰다. 요약본이 원문에
    통째로 들어 있는 관계를 자카드는 길이 차 때문에 낮게 본다.
    """
    sig = {doc: _shingles(t) for doc, t in docs.items() if len(t) > 200}
    out: list[tuple[str, str, float]] = []
    for a, b in combinations(sorted(sig), 2):
        sa, sb = sig[a], sig[b]
        if not sa or not sb:
            continue
        ratio = len(sa & sb) / min(len(sa), len(sb))
        if ratio >= NEAR_DUP:
            out.append((a, b, ratio))
    return sorted(out, key=lambda x: -x[2])


def table_to_markdown(table: list[list[str | None]]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in table if row]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *body = rows
    out = ["| " + " | ".join(head) + " |",
           "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


# 메뉴는 문서 맨 앞에만 나온다. 이 범위 밖의 반복 줄은 본문으로 취급한다.
NAV_SCAN_LINES = 20
NAV_MAX_CHARS = 40


def detect_nav_lines(texts: list[str]) -> set[str]:
    """같은 호스트 문서들의 앞부분에 반복되는 짧은 줄 = 사이트 메뉴.

    trafilatura 가 `favor_recall=True` 에서 네비게이션을 본문으로 오인하는
    경우가 있다(하이코리아처럼 탭마다 별도 페이지인 사이트에서 두드러진다).
    남겨두면 여러 문서의 첫 청크가 같은 메뉴 목록으로 시작해 검색 변별력이
    떨어진다.

    목록을 손으로 관리하지 않고 **데이터에서 유도**한다 — 사이트 메뉴는
    여러 페이지 앞부분에 똑같이 붙고, 실제 본문 문장은 그렇지 않기 때문이다.
    (공통 '선두'만 보는 방식은 문서마다 추출 범위가 달라 잡히지 않았다.)

    메뉴에 문서 자신의 제목이 섞여 함께 잘리는 경우가 있으나, 제목은
    front-matter 에 남고 03_chunk 가 청크마다 헤더로 다시 붙이므로 손실이 아니다.
    """
    if len(texts) < 2:
        return set()
    counts: dict[str, int] = {}
    for text in texts:
        head = {
            ln.strip()
            for ln in text.split("\n")[:NAV_SCAN_LINES]
            if 0 < len(ln.strip()) <= NAV_MAX_CHARS
        }
        for line in head:
            counts[line] = counts.get(line, 0) + 1
    return {line for line, n in counts.items() if n >= 2}


def strip_nav(text: str, nav: set[str]) -> str:
    """앞부분의 메뉴 줄을 걷어낸다. 본문을 만나면 즉시 멈춘다."""
    if not nav:
        return text
    lines = text.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].strip() in nav):
        i += 1
    if i >= len(lines):
        return text  # 전부 메뉴로 판정되면 자르지 않고 THIN 으로 걸러지게 둔다
    return "\n".join(lines[i:]).strip()


def dedupe_repeats(text: str) -> str:
    """한 페이지 안에서 같은 내용이 여러 번 반복되는 것을 한 번으로 줄인다.

    정부 카드뉴스는 이미지 슬라이드마다 같은 문구를 넣고, 접근성 대체
    텍스트와 본문이 함께 추출되면서 **본문 전체가 통째로 두 번** 나온다
    (KOREA-PHISHING-GUIDE 는 1,021자 중 절반이 중복이었다).

    남겨두면 손해가 두 겹이다 — 같은 문장이 두 번 든 청크는 그만큼 근거를
    덜 담고, BM25 는 단순 빈도를 쓰므로 중복된 문서가 부당하게 높게 뜬다.

    두 형태를 함께 처리한다.
      1) 연속으로 반복되는 줄 (제목이 슬라이드마다 붙는 경우)
      2) 앞에서 이미 나온 **3줄 이상의 연속 블록**

    표(`|` 포함)는 건드리지 않는다. 값이 같은 행이 실제로 존재할 수 있고,
    표를 한 청크에 보존하는 것이 03_chunk 의 전제이기 때문이다.
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [
        ln for i, ln in enumerate(lines)
        if i == 0 or not ln.strip() or ln != lines[i - 1] or "|" in ln
    ]

    kept: list[str] = []
    seen_at: dict[str, list[int]] = {}
    i, n = 0, len(lines)
    while i < n:
        run = 0
        if lines[i].strip() and "|" not in lines[i]:
            for start in seen_at.get(lines[i], ()):
                k = 0
                while (start + k < len(kept) and i + k < n
                       and kept[start + k] == lines[i + k] and "|" not in lines[i + k]):
                    k += 1
                run = max(run, k)
        if run >= DUP_RUN_MIN:
            i += run
            continue
        seen_at.setdefault(lines[i], []).append(len(kept))
        kept.append(lines[i])
        i += 1
    return "\n".join(kept).strip()


def build_front_matter(src: Source, entry: dict, text_hash: str) -> str:
    """문서 메타데이터 (planner §4.1).

    `verified_at` 은 여기서 자동으로 채우지 않는다. 사람이 정제본을 눈으로
    확인한 날이어야 의미가 있고, 자동으로 채우면 "확인했다"는 거짓말이 된다.
    """
    fm = {
        "doc_id": src.doc_id,
        "title": src.title,
        "publisher": src.publisher,
        "publisher_type": src.publisher_type,
        "doc_type": src.doc_type,
        "source_url": src.url,
        "published_at": src.published_at,
        "verified_at": entry.get("verified_at"),  # ★ 사람이 채운다
        "sha256": entry.get("sha256"),            # 원본 바이트 (출처 기록)
        "text_sha256": text_hash,                 # 추출 텍스트 (변경 감지)
        "license_note": src.license_note,
        "topics": src.topics,
        "visa_scope": src.visa_scope,
        "lang": src.lang,
    }
    body = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{body}---\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority", default="P0")
    ap.add_argument("--only")
    ap.add_argument("--verify", action="store_true",
                    help="파일을 쓰지 않고 실질 변경 여부만 판정")
    args = ap.parse_args()

    prios = None if args.priority == "all" else set(args.priority.split(","))
    sources = load_sources(prios)
    if args.only:
        sources = [s for s in sources if s.doc_id == args.only]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    changed: list[str] = []
    thin: list[tuple[str, int]] = []
    written = 0

    # 1단계: 전부 추출한다. 호스트별 공통 선두(사이트 메뉴)를 알아내려면
    # 같은 호스트 문서들을 함께 봐야 하므로 쓰기 전에 모아 둔다.
    extracted: list[tuple[Source, str, str]] = []  # (src, host, text)
    for src in sources:
        raw = src.raw_path
        if raw is None:
            print(f"[skip ] {src.doc_id}  원문 없음 — 01_fetch 를 먼저 실행하세요")
            continue
        if raw.suffix == ".pdf":
            text = extract_pdf(raw)
        elif raw.suffix == ".html":
            text = extract_html(raw)
        elif raw.suffix == ".hwp":
            text = extract_hwp(raw)
        else:
            print(f"[skip ] {src.doc_id}  지원하지 않는 형식: {raw.suffix}")
            continue
        extracted.append((src, urlparse(src.url).hostname or "", text))

    # 2단계: 호스트별 공통 선두를 계산한다. HTML 만 대상 — PDF 는 사이트
    # 메뉴가 없고, 같은 보도자료의 HTML/PDF 쌍이 서로를 오염시키면 안 된다.
    by_host: dict[str, list[str]] = {}
    for src, host, text in extracted:
        if (p := src.raw_path) is not None and p.suffix == ".html":
            by_host.setdefault(host, []).append(text)
    nav_by_host = {h: detect_nav_lines(ts) for h, ts in by_host.items()}
    for host, nav in nav_by_host.items():
        if nav:
            print(f"[nav  ] {host}  메뉴 줄 {len(nav)}종 감지 — 본문 앞에서 제거")

    final: dict[str, str] = {}
    for src, host, text in extracted:
        raw = src.raw_path
        if raw is not None and raw.suffix == ".html":
            text = strip_nav(text, nav_by_host.get(host, set()))

        before = len(text)
        text = dedupe_repeats(text)
        if (cut := before - len(text)) > 0:
            print(f"[dup  ] {src.doc_id}  중복 {cut:,}자 제거 ({cut / before:.0%})")

        entry = manifest.setdefault(src.doc_id, {})
        text_hash = sha256_bytes(text.encode("utf-8"))
        prev_hash = entry.get("text_sha256")

        if len(text) < MIN_BODY_CHARS and not src.allow_short:
            thin.append((src.doc_id, len(text)))
            status = "THIN "
        elif prev_hash is None:
            status = "new  "
        elif prev_hash == text_hash:
            status = "same "
        else:
            status = "CHANGED"
            changed.append(src.doc_id)

        print(f"[{status}] {src.doc_id}  본문 {len(text):,}자  ({raw.suffix})")
        final[src.doc_id] = text

        if holes := empty_columns(text):
            spots = ", ".join(f"{ln}행 {col}열" for ln, col in holes[:4])
            print(f"[표   ] {src.doc_id}  ★ 통째로 빈 열 {len(holes)}개 — {spots}")

        if not args.verify:
            src.processed_path.write_text(
                build_front_matter(src, entry, text_hash) + "\n" + text + "\n",
                encoding="utf-8",
            )
            entry["text_sha256"] = text_hash
            entry["text_chars"] = len(text)
            written += 1

    if not args.verify:
        save_manifest(manifest)

    print(f"\n정제 {written}건")

    if thin:
        print(f"\n⚠ 본문이 너무 짧은 문서 {len(thin)}건 — 추출 실패로 보입니다:")
        for doc_id, n in thin:
            print(f"    {doc_id}: {n}자")
        print("  JS 렌더링 페이지이거나 로그인이 필요한 문서일 수 있습니다.")
        print("  다른 URL(첨부 PDF 등)을 찾거나 sources.yaml 에서 제외하세요.")
        print("  ★ 근거로 쓸 수 없는 문서를 코퍼스에 남겨두면 검색이 빈 근거를 물어옵니다.")

    if dups := near_duplicates(final):
        print(f"\n⚠ 서로 겹치는 문서 {len(dups)}쌍 — 같은 자료가 여러 경로로 들어왔습니다:")
        for a, b, r in dups:
            print(f"    {r:.0%}  {a}  ↔  {b}")
        print("  ★ 상위 5건이 한 문서의 사본으로 채워지면 다른 근거가 밀려나고,")
        print("    같은 문서를 여러 번 맞히므로 Recall 이 실제보다 좋아 보입니다.")
        print("  가장 완전한 판 하나만 남기고 sources.yaml 에서 나머지를 빼세요.")

    if changed:
        print(f"\n⚠ 내용이 실제로 바뀐 문서 {len(changed)}건: {', '.join(changed)}")
        print("  1) 무엇이 바뀌었는지 확인하고")
        print("  2) manifest 의 verified_at 을 갱신하고")
        print("  3) 수치·시행일이 바뀌었다면 docs/fact-check.md 도 재확인하세요.")

    return 1 if thin else 0


if __name__ == "__main__":
    sys.exit(main())

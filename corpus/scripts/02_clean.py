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
    python corpus/scripts/02_clean.py --only FSC-2024-0502-LIMIT
    python corpus/scripts/02_clean.py --verify   # 쓰지 않고 변경 여부만 판정
"""

from __future__ import annotations

import argparse
import sys

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


def extract_html(path) -> str:
    """trafilatura 로 본문만 뽑는다 (머리말·네비게이션·푸터 제거)."""
    html = path.read_bytes().decode("utf-8", errors="replace")
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

    for src, host, text in extracted:
        raw = src.raw_path
        if raw is not None and raw.suffix == ".html":
            text = strip_nav(text, nav_by_host.get(host, set()))

        entry = manifest.setdefault(src.doc_id, {})
        text_hash = sha256_bytes(text.encode("utf-8"))
        prev_hash = entry.get("text_sha256")

        if len(text) < MIN_BODY_CHARS:
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

    if changed:
        print(f"\n⚠ 내용이 실제로 바뀐 문서 {len(changed)}건: {', '.join(changed)}")
        print("  1) 무엇이 바뀌었는지 확인하고")
        print("  2) manifest 의 verified_at 을 갱신하고")
        print("  3) 수치·시행일이 바뀌었다면 docs/fact-check.md 도 재확인하세요.")

    return 1 if thin else 0


if __name__ == "__main__":
    sys.exit(main())

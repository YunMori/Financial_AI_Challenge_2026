"""③ 청킹 — 정제본을 검색 단위로 자르고 메타데이터를 상속시킨다 (planner §4.4).

규칙
----
- 문단 단위 분할, 목표 600~900자, 최대 1,200자
- **조·항은 쪼개지 않는다** (법령·고시). 한 조가 최대치를 넘어도 통째로 둔다 —
  쪼개면 "제3항" 없이 "제2항"만 검색되어 조건이 빠진 안내가 나간다.
- **표는 통째로 한 청크.** 요건 비교표가 근거인 경우가 많은데, 표가 잘리면
  특정 체류자격 행만 검색되어 다른 자격의 요건으로 오독될 수 있다.
- 각 청크 앞에 `[발행기관 / 제목 / 절 경로]` 헤더를 **텍스트에 포함**시킨다.
  임베딩 품질과 인용 정확도가 같이 올라간다.
- 인접 청크 100자 오버랩

출력: `corpus/chunks.jsonl`
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import frontmatter

from _common import CHUNKS_JSONL, PROCESSED_DIR, tidy

TARGET_MIN = 600
TARGET_MAX = 900
HARD_MAX = 1200
OVERLAP = 100

# 조·항 시작. 이 줄이 나오면 앞에서 끊는다.
ARTICLE_RE = re.compile(r"^\s*(제\s*\d+\s*[조항]|[①-⑳]|\d+\.\s|[가-힣]\.\s)")
# 절 제목으로 볼 만한 줄 (마크다운 헤딩 또는 짧은 번호 제목)
HEADING_RE = re.compile(r"^\s*(#{1,6}\s+.+|\[\s*\d+\..+?\]|\d+\.\s?[^\s].{0,40})$")
# 표 감지는 상태 기반이다. trafilatura 는 표 행을 `A | B |` 처럼 파이프로
# 시작하지 않게 내보내고, **셀이 길면 다음 줄로 감아서** 파이프가 하나뿐인
# 연속 줄을 만든다. 줄 단위 정규식만으로는 그 지점에서 표가 끊기고,
# 그러면 특정 체류자격 행만 검색되어 다른 자격의 요건으로 오독될 수 있다.
SEPARATOR_RE = re.compile(r"^\s*\|?\s*-{2,}\s*\|")


def is_table_start(line: str) -> bool:
    """표의 시작 행: 파이프 2개 이상이거나 `---|---` 구분선."""
    return line.count("|") >= 2 or bool(SEPARATOR_RE.match(line))


# 표 안에서 파이프 없는 줄이 이만큼 연속되면 표가 끝났다고 본다.
# 감긴 셀은 보통 1~2줄이므로 3이면 충분하다.
TABLE_PROSE_TOLERANCE = 3


def split_blocks(text: str) -> list[tuple[str, str]]:
    """본문을 (종류, 내용) 블록으로 나눈다. 종류: 'table' | 'para'.

    표는 연속된 `|` 줄을 하나로 묶어 절대 쪼개지지 않게 한다.
    """
    blocks: list[tuple[str, str]] = []
    buf: list[str] = []
    table: list[str] = []

    def flush_para() -> None:
        if chunk := "\n".join(buf).strip():
            # 정부 안내문은 빈 줄 없이 줄바꿈만으로 문단을 나누는 경우가 많다.
            # 그대로 두면 문서 전체가 한 블록이 되어 분할이 아예 일어나지 않는다.
            if len(chunk) > TARGET_MAX and "\n" in chunk:
                blocks.extend(("para", ln) for ln in chunk.split("\n") if ln.strip())
            else:
                blocks.append(("para", chunk))
        buf.clear()

    def flush_table() -> None:
        if rows := "\n".join(table).strip():
            blocks.append(("table", rows))
        table.clear()

    prose_run = 0
    for line in text.split("\n"):
        if table:
            # 감긴 셀은 파이프가 없는 줄로 이어진다("일반연수(D-4) |" 다음의
            # "·대학부설어학연수 : 재학증명서"). 파이프 유무로 끊으면 표가
            # 조각나므로, 빈 줄이나 연속된 산문을 만날 때까지 흡수한다.
            if line.strip():
                prose_run = 0 if "|" in line else prose_run + 1
                if prose_run < TABLE_PROSE_TOLERANCE:
                    table.append(line)
                    continue
                # 산문이 이어졌다 — 잘못 흡수한 줄을 문단으로 되돌린다
                buf.extend(table[-(TABLE_PROSE_TOLERANCE - 1):])
                del table[-(TABLE_PROSE_TOLERANCE - 1):]
            prose_run = 0
            flush_table()
        if is_table_start(line):
            flush_para()
            table.append(line)
            continue
        if not line.strip():
            flush_para()
        else:
            buf.append(line)
    flush_para()
    flush_table()
    return blocks


def track_heading(line: str, path: list[str]) -> list[str]:
    """마크다운 헤딩이면 절 경로를 갱신한다."""
    if m := re.match(r"^(#{1,6})\s+(.+)$", line.strip()):
        depth = len(m.group(1))
        return path[: depth - 1] + [m.group(2).strip()]
    return path


def pack(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """블록을 목표 크기로 묶는다. 반환: (절 경로, 본문)"""
    out: list[tuple[str, str]] = []
    cur: list[str] = []
    cur_len = 0
    path: list[str] = []
    cur_path: list[str] = []

    def flush() -> None:
        nonlocal cur, cur_len, cur_path
        if body := "\n".join(cur).strip():
            out.append((" > ".join(cur_path), body))
        cur, cur_len = [], 0

    for kind, body in blocks:
        for line in body.split("\n"):
            path = track_heading(line, path)

        # 표는 단독 청크. 목표 크기를 넘어도 쪼개지 않는다.
        if kind == "table":
            flush()
            out.append((" > ".join(path), body))
            cur_path = path
            continue

        starts_article = bool(ARTICLE_RE.match(body))
        would_exceed = cur_len + len(body) > TARGET_MAX

        # 조·항 경계이거나 목표 크기를 넘으면 여기서 끊는다.
        if cur and (starts_article or would_exceed):
            flush()

        if not cur:
            cur_path = path
        cur.append(body)
        cur_len += len(body) + 1

        # 한 블록이 통째로 최대치를 넘으면 (긴 조문 등) 그대로 내보낸다.
        if cur_len >= HARD_MAX:
            flush()

    flush()
    return out


def add_overlap(chunks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """인접 청크의 꼬리 100자를 다음 청크 앞에 덧붙인다.

    경계에 걸친 문장이 어느 쪽에서도 검색되지 않는 것을 막는다.
    표 청크는 오버랩을 붙이지 않는다 — 표 앞에 문장 조각이 붙으면
    마크다운 표가 깨져 LLM 이 열 구조를 잃는다.
    """
    out: list[tuple[str, str]] = []
    for i, (path, body) in enumerate(chunks):
        if i > 0 and not body.lstrip().startswith("|"):
            tail = chunks[i - 1][1][-OVERLAP:].strip()
            if tail:
                body = f"…{tail}\n\n{body}"
        out.append((path, body))
    return out


def build_chunks(doc) -> list[dict]:
    meta = doc.metadata
    doc_id = meta["doc_id"]
    header = f"[{meta['publisher']} / {meta['title']}"

    packed = add_overlap(pack(split_blocks(tidy(doc.content))))
    rows: list[dict] = []
    for seq, (path, body) in enumerate(packed):
        # 절 경로까지 포함한 헤더를 본문에 넣어 함께 임베딩한다.
        prefix = f"{header} / {path}]" if path else f"{header}]"
        text = f"{prefix}\n{body}"
        rows.append({
            "chunk_id": f"{doc_id}#{seq:04d}",
            "doc_id": doc_id,
            "seq": seq,
            "text": text,
            "heading_path": path,
            "char_count": len(text),
            "title": meta["title"],
            "publisher": meta["publisher"],
            "publisher_type": meta["publisher_type"],
            "doc_type": meta["doc_type"],
            "published_at": meta.get("published_at"),
            "verified_at": meta.get("verified_at"),
            "topics": meta.get("topics") or [],
            "visa_scope": meta.get("visa_scope") or ["ALL"],
            "source_url": meta["source_url"],
            "lang": meta.get("lang", "ko"),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only")
    args = ap.parse_args()

    paths = sorted(PROCESSED_DIR.glob("*.md"))
    if args.only:
        paths = [p for p in paths if p.stem == args.only]
    if not paths:
        print("정제본이 없습니다. 02_clean.py 를 먼저 실행하세요.")
        return 1

    all_rows: list[dict] = []
    no_verified: list[str] = []
    for path in paths:
        doc = frontmatter.load(path)
        rows = build_chunks(doc)
        all_rows.extend(rows)
        sizes = [r["char_count"] for r in rows]
        # 표 청크 = 파이프가 있는 줄이 3줄 이상. trafilatura 표는 파이프로
        # 시작하지 않으므로 "\n|" 로 세면 하나도 잡히지 않는다.
        tables = sum(1 for r in rows
                     if sum("|" in ln for ln in r["text"].split("\n")) >= 3)
        print(f"{doc.metadata['doc_id']:34} 청크 {len(rows):>3}개  "
              f"{min(sizes):>4}~{max(sizes):>5}자  표 {tables}개")
        if not doc.metadata.get("verified_at"):
            no_verified.append(doc.metadata["doc_id"])

    CHUNKS_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n",
        encoding="utf-8",
    )
    sizes = [r["char_count"] for r in all_rows]
    print(f"\n총 {len(all_rows)}청크 · 평균 {sum(sizes) // len(sizes):,}자 "
          f"· 최대 {max(sizes):,}자  →  {CHUNKS_JSONL.relative_to(CHUNKS_JSONL.parents[1])}")

    if no_verified:
        print(f"\n⚠ verified_at 이 비어 있는 문서 {len(no_verified)}건:")
        for doc_id in no_verified:
            print(f"    {doc_id}")
        print("  원문을 눈으로 확인한 뒤 corpus/manifest.json 의 verified_at 을 채우세요.")
        print("  ★ 이 값이 비면 화면의 시점 경고(§8.4)가 동작하지 않습니다.")
        # 청커가 보는 것은 processed/*.md 의 front-matter 이고, 그 값은 02_clean 이
        # 매니페스트에서 옮겨 적는다. 매니페스트만 고치고 여기로 오면 같은 경고가
        # 다시 나온다 — 실제로 한 번 겪었다.
        print("  → 채운 뒤 02_clean.py 를 다시 돌려야 front-matter 에 반영됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

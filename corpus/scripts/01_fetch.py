"""① 수집 — 원문을 내려받아 `corpus/raw/` 에 저장하고 sha256 을 기록한다.

두 가지를 강제한다.

1. **도메인 화이트리스트** — 1차 출처가 아니면 거부한다(`_common.assert_allowed`).
2. **해시 기록** — 재수집 시 원문이 바뀌면 즉시 드러난다. 근거 기반 서비스에서
   근거가 조용히 바뀌는 것은 환각보다 위험하다. 화면에는 여전히
   "출처: 금융위원회 / 확인 2026-08-11"이 붙어 있기 때문이다.

`corpus/raw/` 는 커밋하지 않는다(planner §5.2 저작권). 커밋하는 것은
`corpus/manifest.json` 의 해시와 메타데이터, 그리고 `processed/` 의 정제본뿐이다.

사용법
------
    python corpus/scripts/01_fetch.py                # P0 만
    python corpus/scripts/01_fetch.py --priority all # 전체
    python corpus/scripts/01_fetch.py --force        # 이미 받은 것도 다시
    python corpus/scripts/01_fetch.py --verify       # 받지 않고 해시만 대조
"""

from __future__ import annotations

import argparse
import sys

import httpx

from _common import (
    RAW_DIR,
    Source,
    http_client,
    load_manifest,
    load_sources,
    now_iso,
    save_manifest,
    sha256_bytes,
    sniff_ext,
)


def fetch_one(src: Source) -> tuple[bytes, dict]:
    with http_client(legacy_tls=src.legacy_tls) as client:
        resp = client.get(src.url)
        resp.raise_for_status()
    return resp.content, {
        "http_status": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "final_url": str(resp.url),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority", default="P0",
                    help="P0 | P0,P1 | all  (기본: P0)")
    ap.add_argument("--force", action="store_true", help="이미 받은 문서도 재수집")
    ap.add_argument("--verify", action="store_true",
                    help="재수집해서 해시만 대조하고 파일은 덮어쓰지 않음")
    ap.add_argument("--only", help="특정 doc_id 하나만")
    args = ap.parse_args()

    prios = None if args.priority == "all" else set(args.priority.split(","))
    sources = load_sources(prios)
    if args.only:
        sources = [s for s in sources if s.doc_id == args.only]
    if not sources:
        print("대상 소스가 없습니다. corpus/sources.yaml 을 확인하세요.")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    changed: list[str] = []
    failed: list[tuple[str, str]] = []
    fetched = skipped = 0

    for src in sources:
        prev = manifest.get(src.doc_id, {})
        if src.raw_path is not None and not (args.force or args.verify):
            print(f"[skip ] {src.doc_id}  (이미 있음)")
            skipped += 1
            continue

        try:
            body, meta = fetch_one(src)
        except httpx.HTTPError as e:
            reason = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"[FAIL ] {src.doc_id}  {reason}")
            if "SSLV3_ALERT_HANDSHAKE_FAILURE" in str(e) and not src.legacy_tls:
                print("         -> sources.yaml 에 legacy_tls: true 를 추가해 보세요 "
                      "(구형 TLS 설정 기관 사이트)")
            failed.append((src.doc_id, reason))
            continue

        digest = sha256_bytes(body)
        old_digest = prev.get("sha256")
        ext = sniff_ext(body, meta["content_type"])
        if ext == ".bin":
            print(f"[WARN ] {src.doc_id}  형식을 판별하지 못했습니다 "
                  f"(content-type={meta['content_type']!r}). 02_clean 이 건너뜁니다.")

        # 원본 바이트 해시는 **출처 기록용**이지 변경 감지용이 아니다.
        # 정부 포털 HTML 은 조회수 카운터와 세션 토큰이 매 요청 바뀌므로
        # 바이트 해시로 감지하면 매번 "바뀜"이 뜬다. 항상 울리는 경보는
        # 무시하게 되고, 정작 내용이 바뀐 날 놓친다.
        # 실질 변경 판정은 02_clean 이 추출 텍스트 해시(text_sha256)로 한다.
        if old_digest is None:
            print(f"[new  ] {src.doc_id}  {len(body):,}B  {ext}")
        elif old_digest == digest:
            print(f"[same ] {src.doc_id}  바이트 동일")
        else:
            changed.append(src.doc_id)
            print(f"[bytes] {src.doc_id}  바이트가 다름 (동적 요소일 수 있음)")

        if not args.verify:
            # 확장자가 바뀌었을 수 있으므로(URL 접미사 오판) 기존 파일을 먼저 치운다
            if (old := src.raw_path) is not None and old.suffix != ext:
                old.unlink()
            src.raw_path_for(ext).write_bytes(body)
            fetched += 1

        manifest[src.doc_id] = {
            **prev,
            "url": src.url,
            "title": src.title,
            "publisher": src.publisher,
            "publisher_type": src.publisher_type,
            "doc_type": src.doc_type,
            "published_at": src.published_at,
            "topics": src.topics,
            "visa_scope": src.visa_scope,
            "priority": src.priority,
            "license_note": src.license_note,
            "sha256": digest,
            "bytes": len(body),
            "fetched_at": now_iso(),
            # ★ 내가 원문을 눈으로 확인한 날. 자동으로 채우지 않는다 —
            #   02_clean 에서 정제본을 검수한 뒤 사람이 기록한다.
            "verified_at": prev.get("verified_at"),
            **meta,
        }

    save_manifest(manifest)

    print(f"\n수집 {fetched}건 · 건너뜀 {skipped}건 · 실패 {len(failed)}건")
    if changed:
        print(f"\n바이트가 달라진 문서 {len(changed)}건: {', '.join(changed)}")
        print("  대개는 조회수·세션토큰 같은 동적 요소입니다.")
        print("  실질 변경 여부는 02_clean 이 추출 텍스트로 판정합니다 — 이어서 실행하세요.")
    if failed:
        print(f"\n실패 {len(failed)}건:")
        for doc_id, reason in failed:
            print(f"  {doc_id}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

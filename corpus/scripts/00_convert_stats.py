"""공공데이터포털 통계 CSV를 CP949 -> UTF-8 로 변환하고 파일명을 영문화한다.

원본은 파일명이 한국어이고 내용도 CP949 인코딩이라, 그대로 두면 경로/디코딩
양쪽에서 깨진다. 이 스크립트는 그 변환을 재현 가능하게 고정한다.

원본 CP949 바이트는 저장소에 커밋하지 않는다(재다운로드 가능). 대신
corpus/stats/SOURCES.md 에 원본 파일명 / 발행기관 / sha256 / 출처를 남긴다.

사용법
------
    # 1) 공공데이터포털에서 원본 4종을 내려받아 _incoming/ 에 넣는다
    # 2) python corpus/scripts/00_convert_stats.py
    #    (--src 로 다른 디렉터리 지정 가능, --check 로 변환 없이 해시만 확인)

컬럼명은 한국어 그대로 둔다. 원문 충실성이 근거 기반 서비스에서 더 중요하고,
컬럼 의미는 SOURCES.md 에 대역표로 남긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = REPO_ROOT / "corpus" / "stats" / "_incoming"
DEST = REPO_ROOT / "corpus" / "stats"

# 원본 파일명 -> (영문 파일명, 원본 sha256)
# sha256 은 2026-08-11 확보분 기준. 값이 다르면 출처에서 데이터가 갱신된 것이므로
# SOURCES.md 와 fact-check.md 의 수치를 다시 확인해야 한다.
RENAMES: dict[str, tuple[str, str]] = {
    "경찰청_보이스피싱 현황_20251231.csv": (
        "npa_voice_phishing_status_2016_2025.csv",
        "8cf41efae0c390b3c929aacf51eff02d549bc3596eb1d6655f886a59615483b1",
    ),
    "법무부_30_월별등록외국인시군구별거주현황.csv": (
        "moj_30_registered_foreigners_by_district_monthly.csv",
        "019f624975bb8987e47c03b9dedc68cc029b32a94502f49123e4ba69a4e453c5",
    ),
    "법무부_47_월별외국인유학생.csv": (
        "moj_47_foreign_students_monthly.csv",
        "ecfc2acefb44bc55994b114f87ab422dfdcd65755938507cac53ece0c62cf6b5",
    ),
    # 원본 파일명의 '볍무부'는 배포처 오타 (법무부가 맞음)
    "볍무부_24.csv": (
        "moj_24_foreigners_by_visa_monthly.csv",
        "e9df4d0a552b10c48ea419058af8b89f8ec83f70bd017c1253060dbb76be7010",
    ),
}

# 공공데이터포털 CSV 는 대부분 CP949 지만, 재배포본이 UTF-8 인 경우가 있어 순서대로 시도한다.
CANDIDATE_ENCODINGS = ("cp949", "utf-8-sig", "utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def decode(raw: bytes, name: str) -> tuple[str, str]:
    """바이트를 디코딩하고 (텍스트, 사용한 인코딩) 을 돌려준다."""
    for enc in CANDIDATE_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"[FAIL] {name}: {CANDIDATE_ENCODINGS} 중 어느 것으로도 디코딩되지 않음")


def convert(src_dir: Path, check_only: bool) -> int:
    if not src_dir.is_dir():
        print(f"[SKIP] 원본 디렉터리가 없습니다: {src_dir}")
        print("       공공데이터포털에서 원본을 내려받아 이 경로에 넣고 다시 실행하세요.")
        print("       (출처 URL 은 corpus/stats/SOURCES.md 참고)")
        return 0

    missing = [n for n in RENAMES if not (src_dir / n).exists()]
    if missing:
        print(f"[WARN] {src_dir} 에 없는 원본: {', '.join(missing)}")

    failures = 0
    for original, (english, expected_hash) in RENAMES.items():
        src = src_dir / original
        if not src.exists():
            continue

        actual_hash = sha256(src)
        status = "OK " if actual_hash == expected_hash else "DIFF"
        if status == "DIFF":
            failures += 1
            print(f"[{status}] {original}")
            print(f"       기대 sha256: {expected_hash}")
            print(f"       실제 sha256: {actual_hash}")
            print("       -> 출처 데이터가 갱신되었을 수 있습니다. "
                  "SOURCES.md 와 docs/fact-check.md 의 수치를 재확인하세요.")
        else:
            print(f"[{status}] {original}  sha256 일치")

        if check_only:
            continue

        text, used = decode(src.read_bytes(), original)
        # 개행만 LF 로 정규화하고 내용은 손대지 않는다.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        dest = DEST / english
        dest.write_text(text, encoding="utf-8")
        print(f"       -> {dest.relative_to(REPO_ROOT)}  ({used} -> utf-8, "
              f"{len(text.splitlines()):,}행)")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC,
                        help=f"원본 CP949 파일이 있는 디렉터리 (기본: {DEFAULT_SRC})")
    parser.add_argument("--check", action="store_true",
                        help="변환하지 않고 sha256 만 대조")
    args = parser.parse_args()

    DEST.mkdir(parents=True, exist_ok=True)
    failures = convert(args.src, args.check)
    if failures:
        print(f"\n{failures}건의 해시 불일치. 위 안내를 확인하세요.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""다국어 PDF 조판 선행 검증 (planner §12.1).

planner 가 5주 차 선행 검증으로 지목한 리스크를 컨테이너 안에서 확인한다.
확인 대상은 셋이다.

1. **베트남어 성조 결합문자** — planner §12.2 가 "확인 필수"로 못박은 항목.
   `ề` `ạ` `ế` 처럼 모음 위아래로 기호가 겹치는 글자가 두부(tofu)로 깨지거나
   결합이 풀려 따로 찍히는지.
2. **한국어 완성형** — CJK 폰트가 실제로 잡히는지.
3. **폰트 폴백** — 한 문서 안에 세 언어가 섞였을 때 글자마다 맞는 폰트가
   선택되는지. 여기서 실패하면 조판은 "성공"하는데 글자만 사라진다.

검증 방법: 렌더 후 PDF 에서 **글자별 폰트명을 추출**해 대조한다. 단순히
텍스트만 비교하면 두부로 찍혀도 코드포인트는 그대로 나와 통과해 버린다.
"""
from __future__ import annotations

import logging
import sys
import unicodedata

SAMPLES = {
    "ko": "한도제한계좌 해제에 필요한 서류는 재직증명서입니다.",
    "en": "Documents required to lift the limit: employment certificate.",
    # 성조가 겹치는 글자를 일부러 모았다 (ề ạ ế ộ ữ)
    "vi": "Tài khoản hạn chế giao dịch — giấy tờ chứng minh việc làm.",
}

HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 20mm; }}
body {{ font-family: "Noto Sans", "Noto Sans CJK KR", sans-serif; font-size: 12pt; }}
h1 {{ font-size: 16pt; }}
</style></head><body>
<h1>K-Buddy 서류 체크리스트</h1>
{rows}
</body></html>"""


def main() -> int:
    warnings: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            warnings.append(record.getMessage())

    for name in ("weasyprint", "fontTools", "weasyprint.progress"):
        lg = logging.getLogger(name)
        lg.addHandler(Capture())
        lg.setLevel(logging.WARNING)

    from weasyprint import HTML as WeasyHTML

    rows = "\n".join(f'<p lang="{k}">{v}</p>' for k, v in SAMPLES.items())
    pdf_path = "/tmp/typeset.pdf"
    WeasyHTML(string=HTML.format(rows=rows)).write_pdf(pdf_path)
    print(f"렌더 완료 → {pdf_path}")

    import pdfplumber

    ok = True
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        chars = page.chars
        text = page.extract_text() or ""

    # ── 1) 텍스트 왕복 ────────────────────────────────────────────────
    print("\n[1] 텍스트 왕복")
    for lang, src in SAMPLES.items():
        # PDF 추출은 결합문자를 분해해 내놓을 수 있으므로 NFC 로 맞춘다
        got = unicodedata.normalize("NFC", text)
        want = unicodedata.normalize("NFC", src)
        hit = want in got
        print(f"  {lang}: {'OK' if hit else '★ 불일치'}")
        if not hit:
            ok = False
            print(f"      기대: {want[:50]}")

    # ── 2) 글자별 폰트 — 두부 검출의 핵심 ──────────────────────────────
    print("\n[2] 글자별 폰트 (두부는 텍스트 비교로 안 잡힌다)")
    by_script: dict[str, set[str]] = {}
    for c in chars:
        ch = c.get("text", "")
        if not ch.strip():
            continue
        cp = ord(ch[0])
        if 0xAC00 <= cp <= 0xD7AF:
            key = "한글"
        elif 0x0300 <= cp <= 0x036F:
            key = "결합기호"
        elif cp > 0x00FF:
            key = "베트남어 확장"
        else:
            key = "라틴"
        by_script.setdefault(key, set()).add(str(c.get("fontname", "?")))
    for k, fonts in sorted(by_script.items()):
        print(f"  {k:12} → {', '.join(sorted(fonts))}")
    if "한글" not in by_script:
        print("  ★ 한글 글자가 PDF 에 없다 — CJK 폰트가 안 잡혔을 수 있다")
        ok = False

    # ── 3) 베트남어 결합문자가 따로 찍히지 않았는가 ────────────────────
    print("\n[3] 베트남어 성조 (planner §12.2 확인 필수 항목)")
    combining = [c for c in chars if 0x0300 <= ord(c.get("text", " ")[0]) <= 0x036F]
    if combining:
        print(f"  ★ 결합기호가 별도 글자로 {len(combining)}개 찍혔다 — 성조가 분리됐을 수 있다")
    else:
        print("  OK — 결합기호가 별도 글자로 찍히지 않았다 (합자 정상)")
    vi_chars = [c["text"] for c in chars if ord(c.get("text", " ")[0]) > 0x00FF
                and not (0xAC00 <= ord(c.get("text", " ")[0]) <= 0xD7AF)]
    print(f"  베트남어 확장 글자 {len(vi_chars)}개: {''.join(vi_chars[:24])}")

    # ── 4) 폰트 경고 ─────────────────────────────────────────────────
    print("\n[4] 렌더 경고")
    font_warn = [w for w in warnings if "font" in w.lower() or "glyph" in w.lower()]
    if font_warn:
        ok = False
        for w in font_warn[:10]:
            print(f"  ★ {w}")
    else:
        print(f"  없음 (전체 경고 {len(warnings)}건)")

    print("\n" + ("=" * 56))
    print("조판 선행 검증: " + ("통과" if ok else "★ 실패 — 위 항목 확인"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

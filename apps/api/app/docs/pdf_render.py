"""체크리스트 PDF 렌더 (F3, planner §12).

**창구에서 그대로 보여주는 용도**다. 그래서 설계가 화면과 다르다 —
모국어/한국어 2열 병기, 12pt 이상, 흑백 인쇄 대비 고대비, 체크박스.

★ `@font-face` + `unicode-range` CSS 를 쓰지 않는다.
  planner §12.2 는 언어별 `@font-face` 를 지시했지만, 컨테이너 조판 검증에서
  **불필요함이 확인됐다** — fontconfig 가 글자마다 알아서 폴백한다
  (`scripts/typeset/README.md`, `docs/spec-changes.md` #25).
  쓰지 않는 이유를 여기 적어 두지 않으면 "빠뜨린 것"으로 보인다.

★ WeasyPrint 는 **Windows·macOS 로컬에서 import 자체가 실패하는 것이 정상**이다
  (libpango/libgobject 부재). 그래서 이 모듈은 **함수 안에서 import 한다** —
  모듈 최상단에 두면 PDF 를 쓰지 않는 경로까지 로컬에서 죽는다.
  렌더 검증은 컨테이너에서만 가능하다: `python -m app.docs.pdf_render --self-check`
"""

from __future__ import annotations

import html
import logging
from datetime import date

from app.i18n import checklist as strings
from app.schemas.checklist import ChecklistResponse
from app.schemas.common import EvidenceStatus, Lang

log = logging.getLogger(__name__)

# 인쇄물이다. 화면용 회색(#6b7280)은 흑백 레이저에서 사라진다.
CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans", "Noto Sans CJK KR", sans-serif;
       font-size: 12pt; line-height: 1.5; color: #000; }
h1 { font-size: 17pt; margin: 0 0 2mm 0; }
h2 { font-size: 13pt; margin: 7mm 0 2mm 0; padding-bottom: 1mm;
     border-bottom: 1.2pt solid #000; }
.profile { font-size: 10.5pt; margin: 0 0 4mm 0; }
.profile span { margin-right: 5mm; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0; }
th, td { border: 0.8pt solid #000; padding: 2.2mm 2.5mm; text-align: left;
         vertical-align: top; }
th { font-size: 10.5pt; background: #e8e8e8; }
td.box { width: 9mm; text-align: center; font-size: 14pt; }
td.ko { font-size: 11.5pt; }
.caveat { border: 1.2pt solid #000; padding: 2.5mm 3mm; margin: 2mm 0;
          font-size: 10.5pt; }
.notes { font-size: 10.5pt; margin: 1.5mm 0; }
.none { font-size: 11pt; padding: 2.5mm 3mm; border: 0.8pt dashed #000; }
.sources { font-size: 9.5pt; margin: 1mm 0 0 0; padding: 0; list-style: none; }
.sources li { margin: 0.8mm 0; word-break: break-all; }
footer { margin-top: 8mm; padding-top: 2.5mm; border-top: 0.8pt solid #000;
         font-size: 9.5pt; }
footer p { margin: 1mm 0; }
"""

_DOC = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<title>{title}</title><style>{css}</style></head><body>
<h1>{title}</h1>
<p class="profile">{profile}</p>
{sections}
<footer>{footer}</footer>
</body></html>"""


def _esc(text: str) -> str:
    return html.escape(text or "")


def _profile_line(doc: ChecklistResponse) -> str:
    lang = doc.lang
    parts: list[str] = []
    if doc.visa:
        parts.append(f"{strings.pick(strings.PROFILE_LABELS['visa'], lang)}: {_esc(doc.visa)}")
    if doc.purpose:
        parts.append(f"{strings.pick(strings.PROFILE_LABELS['purpose'], lang)}: {_esc(doc.purpose)}")
    if doc.institution:
        parts.append(
            f"{strings.pick(strings.PROFILE_LABELS['institution'], lang)}: {_esc(doc.institution)}"
        )
    parts.append(
        f"{strings.pick(strings.PROFILE_LABELS['generated_at'], lang)}: {doc.generated_at}"
    )
    return "".join(f"<span>{p}</span>" for p in parts)


def _section_html(section, lang: Lang, include_ko: bool) -> str:
    out = [f"<h2>{_esc(section.title)}</h2>"]

    if section.caveat:
        out.append(f'<div class="caveat">{_esc(section.caveat)}</div>')

    if section.items:
        head = f"<th></th><th>{_esc(strings.pick(strings.COL_LOCAL, lang))}</th>"
        if include_ko and lang is not Lang.KO:
            head += f"<th>{_esc(strings.pick(strings.COL_KO, lang))}</th>"
        rows = []
        for item in section.items:
            # ☐ 는 CJK 폰트에 있다. 조판 검증에서 폴백이 확인된 범위다.
            cells = f'<td class="box">☐</td><td>{_esc(item.label)}</td>'
            if include_ko and lang is not Lang.KO:
                cells += f'<td class="ko">{_esc(item.label_ko)}</td>'
            rows.append(f"<tr>{cells}</tr>")
        out.append(f"<table><tr>{head}</tr>{''.join(rows)}</table>")
    elif section.status is EvidenceStatus.UNKNOWN:
        # ★ 빈 절을 지우지 않는다. "확인하지 못했다"는 것 자체가 답이다.
        out.append(f'<div class="none">{_esc(strings.pick(strings.NOT_CONFIRMED, lang))}</div>')

    if section.notes:
        out.append(f'<p class="notes">{_esc(section.notes)}</p>')

    if section.evidence:
        label = _esc(strings.pick(strings.SOURCES, lang))
        items = "".join(
            f"<li>{label} · {_esc(e.publisher)}"
            + (f" ({_esc(e.published_at)})" if e.published_at else "")
            + f" — {_esc(e.url)}</li>"
            for e in section.evidence
        )
        out.append(f'<ul class="sources">{items}</ul>')

    return "\n".join(out)


def render_html(doc: ChecklistResponse, include_ko: bool = True) -> str:
    """PDF 의 소스이자 §12.3 폴백(조판 미검증 언어)의 산출물이기도 하다."""
    lang = doc.lang
    footer = (
        f"<p>{_esc(strings.pick(strings.FINAL_AUTHORITY, lang))}</p>"
        f"<p>{_esc(strings.pick(strings.ANTI_PHISHING, lang))}</p>"
    )
    return _DOC.format(
        lang=lang.value,
        title=_esc(strings.pick(strings.TITLE, lang)),
        css=CSS,
        profile=_profile_line(doc),
        sections="\n".join(_section_html(s, lang, include_ko) for s in doc.sections),
        footer=footer,
    )


def render_pdf(doc: ChecklistResponse, include_ko: bool = True) -> bytes:
    """HTML → PDF.

    `weasyprint` 를 여기서 import 하는 이유는 모듈 docstring 참조.
    """
    from weasyprint import HTML  # noqa: PLC0415 — 로컬에서 import 가 실패한다

    return HTML(string=render_html(doc, include_ko)).write_pdf()


# ── 자체 검증 (컨테이너 안에서 실행) ─────────────────────────────────


def _self_check() -> int:
    """조판이 실제로 성립하는지 본다.

    ★ **텍스트 비교만으로는 통과시키지 않는다.** 두부(□)로 찍혀도 코드포인트는
      그대로 추출되므로, 글자가 다 깨진 PDF 가 텍스트 대조를 통과한다.
      `scripts/check_typeset.py` 와 같은 방식으로 **글자별 폰트명**을 본다.
    """
    import unicodedata

    from app.docs.checklist import build_checklist

    warnings: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            warnings.append(f"{record.name}: {record.getMessage()}")

    # ★ 레벨을 **핸들러**에 건다. 로거에 걸면 소용이 없다 — `fontTools.subset`
    #   같은 자식 로거는 자기 레벨로 판정한 뒤 부모의 핸들러로 전파하므로,
    #   부모 로거 레벨을 WARNING 으로 올려도 자식의 INFO 가 그대로 들어온다.
    #   ("Compiling 'glyf' table" 308건이 이 경로로 잡혀 검증이 오탐했다.)
    capture = Capture()
    capture.setLevel(logging.WARNING)
    for name in ("weasyprint", "fontTools"):
        lg = logging.getLogger(name)
        lg.addHandler(capture)
        lg.setLevel(logging.WARNING)

    import pdfplumber

    ok = True
    for lang in (Lang.KO, Lang.EN, Lang.VI):
        doc = build_checklist(lang, "E-9", "salary", today=date(2026, 8, 20))
        out = f"/tmp/checklist_{lang.value}.pdf"
        with open(out, "wb") as f:
            f.write(render_pdf(doc))

        with pdfplumber.open(out) as pdf:
            chars = [c for page in pdf.pages for c in page.chars]

        # pdfplumber 의 `text` 는 합자(ligature) 때문에 2글자 이상일 수 있다.
        # 그대로 `unicodedata.combining()` 에 넘기면 TypeError 가 난다(실측).
        def _combining(ch: str) -> bool:
            return len(ch) == 1 and unicodedata.combining(ch) != 0

        hangul = {c["fontname"] for c in chars if "가" <= c["text"] <= "힯"}
        # 베트남어 성조 문자 — planner §12.2 가 "확인 필수"로 못박은 항목.
        # 결합기호(U+0300~)로 분리돼 찍히는지, 합성완성형으로 붙어 있는지를 함께 본다.
        vi_marks = [c for c in chars
                    if _combining(c["text"]) or "Ạ" <= c["text"] <= "ỹ"]

        print(f"[{lang.value}] 글자 {len(chars)} · 한글 폰트 {sorted(hangul) or '없음'}"
              f" · vi 성조 {len(vi_marks)}")

        if hangul and not any("CJK" in f or "KR" in f for f in hangul):
            print(f"  ✗ 한글이 CJK 폰트로 찍히지 않았다: {sorted(hangul)}")
            ok = False
        if lang is Lang.VI and not vi_marks:
            print("  ✗ 베트남어 성조 글자가 하나도 추출되지 않았다")
            ok = False
        # 결합기호가 별도 글자로 찍히면 결합이 풀린 것이다
        if any(_combining(c["text"]) for c in chars):
            print("  ✗ 결합기호가 별도 글자로 분리됐다")
            ok = False

    if warnings:
        print(f"  ✗ 폰트·글리프 경고 {len(warnings)}건: {warnings[:3]}")
        ok = False

    print("통과" if ok else "실패")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    if "--self-check" in sys.argv:
        raise SystemExit(_self_check())
    from app.docs.checklist import build_checklist

    doc_ = build_checklist(Lang.VI, "E-9", "salary")
    sys.stdout.buffer.write(render_pdf(doc_))

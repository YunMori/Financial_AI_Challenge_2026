"""YAML 카탈로그의 `{stem}_ko` / `{stem}_i18n` 규칙을 푸는 한 곳.

`corpus/matrix/*.yaml` 은 산문을 `notes_ko` + `notes_i18n: {en: …, vi: …}`
형태로 담는다. 그 규칙을 읽는 코드가 F3(`docs/checklist.py`)에 하나,
F8(`matrix/scam.py`)에 하나 있으면 **경고 로그가 한쪽에만 남는다** —
번역이 빠진 것을 알려 주는 장치가 절반만 도는 것이라 여기로 올린다.

★ 폴백이 일어나면 **한국어를 못 읽는 이용자에게 한국어가 보인다.** 주의문구가
  그렇게 되면 "예시일 뿐이고 은행마다 다르다", "100% 사기다" 같은 경고 자체가
  전달되지 않는다. 그래서 조용히 넘기지 않고 경고를 남긴다 — 카탈로그에 공개
  언어를 모두 채우는 것이 원칙이고, 이 로그가 그 미달을 드러내는 유일한 신호다.
"""

from __future__ import annotations

import logging

from app.schemas.common import Lang

log = logging.getLogger(__name__)


def localized(block: dict, stem: str, lang: Lang) -> str:
    """`{stem}_i18n[lang]` → `{stem}_ko` 폴백."""
    source = (block.get(f"{stem}_ko") or "").strip()
    if lang is Lang.KO or not source:
        return source
    translated = (block.get(f"{stem}_i18n") or {}).get(lang.value)
    if translated:
        return translated.strip()
    log.warning("%s_i18n 에 %s 가 없어 한국어로 폴백한다 — 번역을 채워야 한다",
                stem, lang.value)
    return source

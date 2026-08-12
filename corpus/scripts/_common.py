"""코퍼스 파이프라인 공통 모듈.

01_fetch → 02_clean → 03_chunk → 04_index 가 공유하는 경로·화이트리스트·매니페스트.

**지식베이스 오염 방어가 이 모듈의 존재 이유다**(planner §8.4, §11장).
수집 단계에서 도메인을 강제하고 sha256 을 기록해 두어야, 재수집 시 원문이
바뀐 문서를 골라낼 수 있다. 근거 기반 서비스에서 근거가 조용히 바뀌는 것은
환각보다 위험하다 — 화면에는 여전히 "출처: 금융위원회"가 붙어 있기 때문이다.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "corpus"
SOURCES_YAML = CORPUS / "sources.yaml"
RAW_DIR = CORPUS / "raw"  # gitignore — 원문 재배포로 오해될 수 있음
PROCESSED_DIR = CORPUS / "processed"
MANIFEST = CORPUS / "manifest.json"  # ★ 커밋 대상 — 해시 기록이 오염 탐지의 근거
CHUNKS_JSONL = CORPUS / "chunks.jsonl"

# HTTP 헤더는 ASCII 만 허용된다. 한글을 넣으면 httpx 가 UnicodeEncodeError 를 낸다.
USER_AGENT = "KBuddyBot/0.1 (2026 Financial AI Challenge; research use; +contact via repo)"


# ── 도메인 화이트리스트 (planner §5.1, §11장) ────────────────────────
#
# 그 외 도메인은 수집 스크립트가 거부한다. 블로그·커뮤니티·언론 재인용을
# 근거로 쓰면 "1차 출처 기반"이라는 주장 자체가 무너진다.

ALLOWED_SUFFIXES: tuple[str, ...] = (".go.kr", ".korea.kr")

# or.kr 은 누구나 받을 수 있으므로 접미사가 아니라 호스트를 하나씩 명시한다.
ALLOWED_HOSTS: frozenset[str] = frozenset({
    "fss.or.kr",       # 금융감독원
    "kfb.or.kr",       # 전국은행연합회
    "bok.or.kr",       # 한국은행
    "fsec.or.kr",      # 금융보안원
    "kdic.or.kr",      # 예금보험공사
    "kifrs.or.kr",     # 한국금융연구원
})

# 국내 금융기관 공식 도메인. 각 사 공지·약관 페이지를 근거로 쓸 때만 사용한다.
ALLOWED_INSTITUTION_HOSTS: frozenset[str] = frozenset({
    "kbstar.com", "shinhan.com", "shinhanbank.com", "wooribank.com",
    "kebhana.com", "hanabank.com", "nonghyup.com", "nhbank.com",
    "ibk.co.kr", "standardchartered.co.kr", "citibank.co.kr",
})


class SourceNotAllowed(ValueError):
    """화이트리스트 밖 도메인."""


def host_matches(host: str, allowed: frozenset[str]) -> bool:
    """`www.fss.or.kr` 이 `fss.or.kr` 에 매칭되도록 서브도메인을 허용한다."""
    return any(host == a or host.endswith("." + a) for a in allowed)


def assert_allowed(url: str) -> str:
    """화이트리스트를 통과하면 호스트를 돌려주고, 아니면 거부한다."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise SourceNotAllowed(f"호스트를 파싱할 수 없음: {url}")
    if host.endswith(ALLOWED_SUFFIXES):
        return host
    if host_matches(host, ALLOWED_HOSTS | ALLOWED_INSTITUTION_HOSTS):
        return host
    raise SourceNotAllowed(
        f"화이트리스트 밖 도메인: {host}\n"
        f"  1차 출처만 근거로 쓴다. 정부·공공기관(.go.kr) 또는 "
        f"_common.py 의 ALLOWED_HOSTS 에 명시된 기관만 허용된다.\n"
        f"  추가가 필요하면 그 기관이 1차 출처인지 먼저 확인할 것."
    )


def legacy_tls_context() -> ssl.SSLContext:
    """구형 TLS 설정 기관 사이트용 컨텍스트.

    일부 국내 기관 사이트(은행연합회 등)는 현대 OpenSSL 기본값과
    핸드셰이크가 실패한다(`SSLV3_ALERT_HANDSHAKE_FAILURE`).

    **인증서 검증은 그대로 유지하고** 레거시 재협상과 낮은 보안수준만 허용한다.
    검증까지 끄면 중간자 공격에 코퍼스가 열려 §11장의 지식베이스 오염 방어가
    무의미해진다. 이 컨텍스트는 sources.yaml 에서 `legacy_tls: true` 를
    명시한 소스에만 적용된다.
    """
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx


# ── 소스 정의 ────────────────────────────────────────────────────────


@dataclass(slots=True)
class Source:
    """`corpus/sources.yaml` 의 한 항목.

    front-matter 의 대부분이 여기서 온다. **수집하면서 즉시 채운다** —
    나중에 몰아서 하면 published_at 을 다시 찾아야 하고, published_at 이
    틀리면 시점 경고 기능 전체가 무의미해진다(planner §5.3).
    """

    doc_id: str
    url: str
    title: str
    publisher: str
    publisher_type: Literal["government", "fsi", "research"]
    doc_type: Literal["press_release", "guideline", "statute", "faq", "matrix"]
    published_at: str | None = None          # ISO. 미상이면 None 이되 채우는 것이 원칙
    topics: list[str] = field(default_factory=list)
    visa_scope: list[str] = field(default_factory=lambda: ["ALL"])
    lang: str = "ko"
    license_note: str = ""
    priority: Literal["P0", "P1", "P2", "P3"] = "P0"
    legacy_tls: bool = False
    # THIN 판정을 면제한다. **추출은 성공했는데 원문이 짧은** 경우에만 쓰고,
    # note 에 근거를 남긴다. 이유 없이 붙이면 THIN 경보 자체가 무의미해진다.
    allow_short: bool = False
    note: str = ""

    @property
    def raw_path(self) -> Path | None:
        """이미 수집된 원문 경로. 확장자는 수집 시점에 내용으로 판별한다."""
        hits = sorted(RAW_DIR.glob(f"{self.doc_id}.*"))
        return hits[0] if hits else None

    def raw_path_for(self, ext: str) -> Path:
        return RAW_DIR / f"{self.doc_id}{ext}"

    @property
    def processed_path(self) -> Path:
        return PROCESSED_DIR / f"{self.doc_id}.md"


def sniff_ext(body: bytes, content_type: str = "") -> str:
    """실제 내용으로 확장자를 판별한다.

    URL 접미사로 판단하면 안 된다. 정부 사이트의 첨부파일 링크는
    `/comm/getFile?srvcId=BBSTY1&upperNo=82205&…` 처럼 확장자가 없는 경우가
    흔하고, 그대로 두면 PDF 가 `.html` 로 저장되어 02_clean 이 잘못된 파서를
    고른다(실제로 겪은 사고 — dev-log 2026-08-11 참조).
    """
    if body[:5] == b"%PDF-":
        return ".pdf"
    if body[:4] == b"PK\x03\x04":  # hwpx/docx/xlsx 계열 zip 컨테이너
        return ".zip"
    if body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # 구 HWP/MS OLE
        return ".hwp"
    ct = content_type.lower()
    if "pdf" in ct:
        return ".pdf"
    if "html" in ct or "xml" in ct:
        return ".html"
    return ".bin"


def load_sources(priorities: set[str] | None = None) -> list[Source]:
    if not SOURCES_YAML.exists():
        raise SystemExit(f"소스 목록이 없습니다: {SOURCES_YAML}")
    raw = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8")) or {}
    items = raw.get("sources") or []
    out: list[Source] = []
    seen: set[str] = set()
    for item in items:
        src = Source(**item)
        if src.doc_id in seen:
            raise SystemExit(f"doc_id 중복: {src.doc_id}")
        seen.add(src.doc_id)
        assert_allowed(src.url)  # 목록을 읽는 시점에 이미 거부한다
        if priorities is None or src.priority in priorities:
            out.append(src)
    return out


# ── 매니페스트 ───────────────────────────────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def today_iso() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 텍스트 유틸 ──────────────────────────────────────────────────────

_WS = re.compile(r"[ \t 　]+")
_BLANKS = re.compile(r"\n{3,}")


def tidy(text: str) -> str:
    """공백·개행 정리. 내용은 손대지 않는다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_WS.sub(" ", line).rstrip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def http_client(legacy_tls: bool = False) -> httpx.Client:
    return httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        verify=legacy_tls_context() if legacy_tls else True,
    )

"""구조화 출력 스트림에서 `answer` 필드만 뽑아내는 증분 파서.

구조화 출력을 쓰면 응답 전체가 **하나의 JSON 텍스트**로 흘러나온다:

    {"answer": "한도제한계좌란 ...", "tier": "A", "citations": [...]}

이걸 그대로 화면에 흘리면 이용자는 JSON 을 보게 된다. 그렇다고 완성될
때까지 기다리면 TTFT 목표(1.8초)를 지킬 수 없다.

`LLMAnswer` 가 `answer` 를 **첫 필드**로 두는 이유가 이것이다 —
구조화 출력은 스키마 순서대로 생성되므로, 스트림 앞부분이 곧 답변 본문이다.
여는 따옴표 이후부터 이스케이프되지 않은 닫는 따옴표 전까지를 잘라 흘린다.

전체 JSON 파싱은 스트림이 끝난 뒤 SDK 가 하고, 여기서는 **화면에 흘릴 조각만**
만든다. 최종 검증은 `postprocess.finalize()` 가 하며, 검사에 실패하면
`invalidate` 이벤트로 화면을 폴백 카드로 교체한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 찾을 필드. 스키마의 첫 필드와 일치해야 한다.
_KEY = '"answer"'

# JSON 문자열 이스케이프
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b",
            "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


@dataclass(slots=True)
class AnswerStreamer:
    """JSON 조각을 먹여 주면 `answer` 본문 조각을 돌려준다.

    청크 경계가 이스케이프 시퀀스(`\\u00ad` 의 중간 등) 한가운데를 지나갈 수
    있으므로 상태를 들고 있어야 한다.
    """

    _buf: str = ""
    _state: str = "seek_key"   # seek_key → seek_colon → seek_quote → in_string → done
    _escape: bool = False
    _unicode: str | None = None
    _emitted: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self._state == "done"

    @property
    def text(self) -> str:
        """지금까지 흘려보낸 전체 본문."""
        return "".join(self._emitted)

    def feed(self, chunk: str) -> str:
        """조각을 먹이고, 이번에 새로 확정된 본문을 돌려준다 (없으면 빈 문자열)."""
        if self._state == "done" or not chunk:
            return ""

        self._buf += chunk
        out: list[str] = []

        while self._buf:
            if self._state == "seek_key":
                idx = self._buf.find(_KEY)
                if idx < 0:
                    # 키가 청크 경계에 걸릴 수 있으므로 꼬리를 남긴다.
                    self._buf = self._buf[-len(_KEY):]
                    break
                self._buf = self._buf[idx + len(_KEY):]
                self._state = "seek_colon"

            elif self._state == "seek_colon":
                idx = self._buf.find(":")
                if idx < 0:
                    self._buf = ""
                    break
                self._buf = self._buf[idx + 1:]
                self._state = "seek_quote"

            elif self._state == "seek_quote":
                idx = self._buf.find('"')
                if idx < 0:
                    self._buf = ""
                    break
                self._buf = self._buf[idx + 1:]
                self._state = "in_string"

            elif self._state == "in_string":
                consumed, chars, finished = self._consume_string(self._buf)
                self._buf = self._buf[consumed:]
                out.extend(chars)
                if finished:
                    self._state = "done"
                    break
                if consumed == 0:
                    break  # 더 먹여야 진행된다 (이스케이프 중간)

        emitted = "".join(out)
        if emitted:
            self._emitted.append(emitted)
        return emitted

    def _consume_string(self, buf: str) -> tuple[int, list[str], bool]:
        """문자열 본문을 가능한 만큼 소비한다.

        반환: (소비한 글자 수, 확정된 문자들, 문자열이 끝났는가)
        """
        chars: list[str] = []
        i = 0
        while i < len(buf):
            ch = buf[i]

            if self._unicode is not None:
                # **16진수만 가져온다.** 길이만 보고 자르면 깨진 시퀀스에서
                # 다음 이스케이프의 백슬래시까지 삼켜 뒤이은 문자들이
                # 그대로 화면에 새어 나온다.
                need = 4 - len(self._unicode)
                j = i
                while j < len(buf) and (j - i) < need and buf[j] in "0123456789abcdefABCDEF":
                    j += 1
                self._unicode += buf[i:j]
                i = j
                if len(self._unicode) < 4:
                    if j < len(buf):
                        # 16진수가 아닌 문자를 만났다 — 깨진 시퀀스.
                        # 버리고 그 문자부터 정상 처리한다.
                        self._unicode = None
                        continue
                    return i, chars, False  # 청크가 여기서 끊겼다
                chars.append(chr(int(self._unicode, 16)))
                self._unicode = None
                continue

            if self._escape:
                self._escape = False
                if ch == "u":
                    self._unicode = ""
                else:
                    chars.append(_ESCAPES.get(ch, ch))
                i += 1
                continue

            if ch == "\\":
                self._escape = True
                i += 1
                continue

            if ch == '"':
                return i + 1, chars, True  # 이스케이프되지 않은 닫는 따옴표

            chars.append(ch)
            i += 1

        return i, chars, False

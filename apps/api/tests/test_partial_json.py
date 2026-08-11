"""증분 JSON 파서 테스트.

TTFT 1.8초 목표가 이 파서에 걸려 있다. 여기가 틀리면 이용자가 화면에서
JSON 조각이나 깨진 유니코드를 보게 된다.
"""

import json

import pytest

from app.streaming.partial_json import AnswerStreamer


def feed_all(chunks: list[str]) -> tuple[str, AnswerStreamer]:
    s = AnswerStreamer()
    out = "".join(s.feed(c) for c in chunks)
    return out, s


class TestAnswerStreamer:
    def test_single_chunk(self):
        out, s = feed_all(['{"answer": "안녕하세요", "tier": "A"}'])
        assert out == "안녕하세요" and s.done

    def test_split_across_chunks(self):
        out, s = feed_all(['{"ans', 'wer": "한도제', '한계좌", "tier"', ': "A"}'])
        assert out == "한도제한계좌" and s.done

    def test_char_by_char(self):
        """실제 스트림은 토큰 단위로 잘게 온다."""
        src = '{"answer": "이체 한도는 100만원입니다", "tier": "A"}'
        out, s = feed_all(list(src))
        assert out == "이체 한도는 100만원입니다" and s.done

    def test_escaped_quote_does_not_end_string(self):
        """★ 답변에 따옴표가 있으면 여기서 잘못 끊긴다."""
        out, s = feed_all([r'{"answer": "그는 \"한도제한계좌\"라고 했다", "tier": "A"}'])
        assert out == '그는 "한도제한계좌"라고 했다' and s.done

    def test_escaped_backslash(self):
        out, _ = feed_all([r'{"answer": "경로는 C:\\temp 입니다"}'])
        assert out == r"경로는 C:\temp 입니다"

    def test_newline_escape(self):
        out, _ = feed_all([r'{"answer": "첫줄\n둘째줄"}'])
        assert out == "첫줄\n둘째줄"

    def test_unicode_escape(self):
        out, _ = feed_all([r'{"answer": "\ud55c\uae00"}'])
        assert out == "한글"

    def test_unicode_escape_split_across_chunks(self):
        """★ 청크 경계가 \\uXXXX 한가운데를 지나가는 경우.

        "한글" = \\ud55c\\uae00 — 각 시퀀스가 정확히 16진수 4자리다.
        """
        out, _ = feed_all([r'{"answer": "\ud5', "5c", r'\uae', '00"}'])
        assert out == "한글"

    def test_malformed_unicode_does_not_swallow_next_escape(self):
        """깨진 \\uXXX 뒤의 이스케이프까지 삼키면 남은 본문이 그대로 샌다."""
        out, _ = feed_all([r'{"answer": "\ud5\uae00 뒤"}'])
        assert out.endswith("글 뒤"), out

    def test_escape_split_at_backslash(self):
        out, _ = feed_all([r'{"answer": "따옴표', "\\", r'" 입니다"}'])
        assert out == '따옴표" 입니다'

    def test_key_split_across_chunks(self):
        out, s = feed_all(['{"ans', 'wer"', ': ', '"본문"}'])
        assert out == "본문" and s.done

    def test_stops_at_end_of_answer_field(self):
        """이후 필드(tier, citations)는 화면에 흘리지 않는다."""
        out, s = feed_all(['{"answer": "본문", "tier": "A", "citations": [{"ref": 1}]}'])
        assert out == "본문" and s.done

    def test_feed_after_done_is_noop(self):
        s = AnswerStreamer()
        s.feed('{"answer": "본문"}')
        assert s.feed(', "tier": "A"}') == ""

    def test_empty_answer(self):
        """계층 C 는 answer 를 비운다."""
        out, s = feed_all(['{"answer": "", "tier": "C", "out_of_scope": true}'])
        assert out == "" and s.done

    def test_accumulated_text_matches_emitted(self):
        s = AnswerStreamer()
        for c in ['{"answer": "가', "나", '다"}']:
            s.feed(c)
        assert s.text == "가나다"

    @pytest.mark.parametrize(
        "answer_text",
        [
            "한도제한계좌의 이체 한도는 100만원입니다.",
            'Tại sao tôi chỉ chuyển được 1 triệu won?',
            'He said "no" and left.',
            "줄바꿈\n포함\n답변",
            "이모지 🇰🇷 포함",
            "",
        ],
    )
    def test_roundtrip_against_real_json(self, answer_text):
        """json.dumps 로 만든 실제 직렬화를 그대로 복원해야 한다."""
        payload = json.dumps({"answer": answer_text, "tier": "A"}, ensure_ascii=False)
        assert feed_all([payload])[0] == answer_text

    @pytest.mark.parametrize("answer_text", ["따옴표 \" 포함", "역슬래시 \\ 포함", "탭\t포함"])
    def test_roundtrip_ascii_escaped(self, answer_text):
        """ensure_ascii=True 면 한글이 \\uXXXX 로 나온다 — SDK 설정에 따라 다르다."""
        payload = json.dumps({"answer": answer_text, "tier": "A"}, ensure_ascii=True)
        assert feed_all([payload])[0] == answer_text

    def test_answer_must_be_first_field_for_streaming_to_help(self):
        """answer 가 뒤에 있으면 파서는 동작하지만 스트리밍 이점이 사라진다.

        스키마 순서를 지키는 이유를 문서화하는 테스트다.
        """
        out, s = feed_all(['{"tier": "A", "citations": [], "answer": "늦게 온 본문"}'])
        assert out == "늦게 온 본문" and s.done

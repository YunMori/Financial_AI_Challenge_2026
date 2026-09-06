"""외부 API 오류에서 인증키가 새지 않는가 (planner §8.2 의 로깅 원칙 확장).

2026-08-21 실측: FSS 가 307 리다이렉트를 내자 `httpx.HTTPStatusError` 메시지에
요청 URL 이 통째로 들어갔고, 그 URL 의 `auth=` 에 인증키가 있었다. 그대로
`logger.warning()` 과 API 응답의 `unavailable_reason` 으로 나갔다 —
**로그 파일과 브라우저 두 곳에 평문 키가 남는다.**

planner §8.2 는 이용자 입력에 대해 `log_safe()` 를 요구했다. 같은 원칙이
우리 자격증명에도 적용된다.
"""

import httpx
import pytest

from app.tools.redact import redact, safe_reason

FSS_KEY = "f8c178e1d180d63fd2eac58b78848a5d"  # noqa: S105 - 형태만 흉내낸 더미
ECOS_KEY = "ID7N087ZHB8CQ9VGCE9O"  # noqa: S105


class TestRedact:
    def test_fss_query_param_is_masked(self):
        url = f"http://finlife.fss.or.kr/finlifeapi/depositProductsSearch.json?auth={FSS_KEY}&topFinGrpNo=020000"
        out = redact(url)
        assert FSS_KEY not in out
        assert "auth=***" in out
        # 진단에 필요한 나머지는 남아야 한다
        assert "topFinGrpNo=020000" in out
        assert "depositProductsSearch.json" in out

    def test_ecos_path_segment_is_masked(self):
        url = f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/100/731Y001/D/20260811/20260821/0000001"
        out = redact(url)
        assert ECOS_KEY not in out
        assert "/api/StatisticSearch/***/json" in out
        # 통계표 코드는 남아야 원인을 좁힐 수 있다
        assert "731Y001" in out

    @pytest.mark.parametrize("param", ["api_key", "apikey", "serviceKey", "token", "API_KEY"])
    def test_other_credential_params_are_masked(self, param):
        out = redact(f"https://example.go.kr/x?{param}={FSS_KEY}&page=1")
        assert FSS_KEY not in out
        assert "page=1" in out

    def test_plain_text_is_untouched(self):
        msg = "ConnectTimeout: timed out after 5.0s"
        assert redact(msg) == msg


class TestSafeReason:
    def test_httpx_error_message_loses_the_key(self):
        """★ 실제로 새던 경로를 그대로 재현한다."""
        url = f"https://finlife.fss.or.kr/finlifeapi/depositProductsSearch.json?auth={FSS_KEY}&pageNo=1"
        request = httpx.Request("GET", url)
        response = httpx.Response(307, request=request, headers={"location": url})
        exc = httpx.HTTPStatusError("Redirect response '307'", request=request, response=response)

        reason = safe_reason(exc)
        assert FSS_KEY not in reason
        assert "HTTPStatusError" in reason

    def test_exception_type_is_kept(self):
        assert safe_reason(TimeoutError("slow")).startswith("TimeoutError:")


class TestHttpxDoesNotLogTheKey:
    """★★ `redact.py` 로는 막을 수 없는 경로.

    httpx 는 **성공한 요청**의 URL 을 INFO 로 찍는다 — 거기에 인증키가 있다.
    예외 처리와 무관하며, 조회가 성공할수록 키가 더 많이 쌓인다(2026-08-21 실측).
    """

    def test_httpx_logger_is_raised_to_warning(self):
        import logging

        import app.main  # noqa: F401 - 임포트 시점에 레벨이 설정된다

        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING

    def test_our_own_diagnostics_still_visible(self):
        """키를 막느라 진단까지 잠그면 안 된다 — 우리 로거는 그대로다."""
        import logging

        import app.main  # noqa: F401

        assert logging.getLogger("app.routers.products").getEffectiveLevel() <= logging.WARNING


class TestClientsUseIt:
    def test_fss_uses_https(self):
        """평문 http 로 키를 보내지 않는다. 307 리다이렉트도 이걸로 사라진다."""
        from app.tools.fss_client import BASE_URL

        assert BASE_URL.startswith("https://")

    def test_ecos_uses_https(self):
        from app.tools.ecos_client import BASE_URL

        assert BASE_URL.startswith("https://")

    def test_fss_failure_reason_is_redacted(self, monkeypatch):
        """클라이언트가 `safe_reason` 을 우회하지 않는지 끝까지 확인한다."""
        import os

        from app.config import get_settings
        from app.tools.fss_client import FssUnavailable, clear_cache, fetch

        clear_cache()
        os.environ["FSS_API_KEY"] = FSS_KEY
        get_settings.cache_clear()

        def boom(url, params, timeout):
            raise httpx.ConnectError(f"failed connecting to {url}?auth={FSS_KEY}")

        try:
            with pytest.raises(FssUnavailable) as e:
                fetch("deposit", fetcher=boom)
            assert FSS_KEY not in e.value.reason
        finally:
            os.environ.pop("FSS_API_KEY", None)
            get_settings.cache_clear()
            clear_cache()

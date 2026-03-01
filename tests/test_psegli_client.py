"""Tests for PSEGLIClient (mocked requests)."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from custom_components.psegli.psegli import PSEGLIClient, REQUEST_TIMEOUT
from custom_components.psegli.exceptions import InvalidAuth, PSEGLIError


class TestPSEGLIClient:
    """Tests for PSEGLIClient."""

    def test_no_thread_pool_executor_usage(self):
        """Verify no ThreadPoolExecutor in psegli.py."""
        import inspect
        from custom_components.psegli import psegli
        source = inspect.getsource(psegli)
        assert "ThreadPoolExecutor" not in source
        assert "get_event_loop" not in source

    def test_requests_have_timeouts(self):
        """Verify all requests calls include timeout."""
        import inspect
        from custom_components.psegli import psegli
        source = inspect.getsource(psegli)
        # Count session.get and session.post calls — each should have timeout
        import re
        gets = re.findall(r'self\.session\.get\(', source)
        posts = re.findall(r'self\.session\.post\(', source)
        timeouts = re.findall(r'timeout=REQUEST_TIMEOUT', source)
        # Each get/post should have a corresponding timeout
        assert len(timeouts) >= len(gets) + len(posts)

    def test_network_error_raises_psegli_error(self, mock_requests_session):
        """ConnectionError should raise PSEGLIError, not InvalidAuth."""
        mock_requests_session.get.side_effect = requests.exceptions.ConnectionError("DNS failed")

        with patch("custom_components.psegli.psegli.requests.Session", return_value=mock_requests_session):
            client = PSEGLIClient("MM_SID=test")
            client.session = mock_requests_session
            with pytest.raises(PSEGLIError):
                client.test_connection()

    def test_timeout_raises_psegli_error(self, mock_requests_session):
        """Timeout should raise PSEGLIError, not InvalidAuth."""
        mock_requests_session.get.side_effect = requests.exceptions.Timeout("timed out")

        client = PSEGLIClient("MM_SID=test")
        client.session = mock_requests_session
        with pytest.raises(PSEGLIError):
            client.test_connection()

    def test_auth_failure_raises_invalid_auth(self, mock_requests_session):
        """Redirect to login URL should raise InvalidAuth."""
        response = MagicMock()
        response.status_code = 200
        response.url = "https://mysmartenergy.psegliny.com/Login"
        response.raise_for_status = MagicMock()
        mock_requests_session.get.return_value = response

        client = PSEGLIClient("MM_SID=expired")
        client.session = mock_requests_session
        with pytest.raises(InvalidAuth):
            client.test_connection()

    def test_successful_connection(self, mock_requests_session):
        """Successful connection returns True."""
        response = MagicMock()
        response.status_code = 200
        response.url = "https://mysmartenergy.psegliny.com/Dashboard"
        response.raise_for_status = MagicMock()
        mock_requests_session.get.return_value = response

        client = PSEGLIClient("MM_SID=valid")
        client.session = mock_requests_session
        assert client.test_connection() is True

    def test_explicit_dates_respected(self, mock_requests_session):
        """Caller-provided start_date and end_date should be used directly."""
        # Set up mock responses for the full get_usage_data flow
        dashboard_html = '<input name="__RequestVerificationToken" type="hidden" value="token123" />'
        chart_setup_json = json.dumps({"AjaxResults": []})
        chart_data_json = json.dumps({"Data": {"series": []}})

        responses = [
            # _get_dashboard_page GET (also serves as auth gate)
            MagicMock(status_code=200, url="https://mysmartenergy.psegliny.com/Dashboard",
                     text=dashboard_html, raise_for_status=MagicMock()),
            # _get_chart_data GET
            MagicMock(status_code=200, text=chart_data_json, raise_for_status=MagicMock()),
        ]
        mock_requests_session.get.side_effect = responses
        # _setup_chart_context POST
        mock_requests_session.post.return_value = MagicMock(
            status_code=200, text=chart_setup_json, raise_for_status=MagicMock()
        )

        client = PSEGLIClient("MM_SID=test")
        client.session = mock_requests_session

        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 15)
        client.get_usage_data(start_date=start, end_date=end)

        # Verify the dates were passed to _setup_chart_context via the POST
        post_call = mock_requests_session.post.call_args
        post_data = post_call.kwargs.get("data") or post_call[1].get("data")
        assert post_data["Start"] == "2026-01-01"
        assert post_data["End"] == "2026-01-15"

    def test_update_cookie(self):
        """update_cookie changes the session header."""
        client = PSEGLIClient("MM_SID=old")
        client.update_cookie("MM_SID=new_cookie_value")
        assert client.cookie == "MM_SID=new_cookie_value"

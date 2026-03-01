"""Shared test fixtures for PSEG Long Island integration tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_requests_session():
    """Mock requests.Session for PSEGLIClient tests."""
    session = MagicMock()
    # Use MagicMock for headers to allow .update() mocking
    session.headers = MagicMock()

    # Default: Dashboard returns 200, no redirect to login
    response = MagicMock()
    response.status_code = 200
    response.url = "https://mysmartenergy.psegliny.com/Dashboard"
    response.text = '<input name="__RequestVerificationToken" type="hidden" value="test_token_123" />'
    response.raise_for_status = MagicMock()
    session.get.return_value = response
    session.post.return_value = response
    return session


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp.ClientSession for addon client tests."""
    session = AsyncMock()
    return session

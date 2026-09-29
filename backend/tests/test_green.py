"""Green Web Foundation client tests (MASTERSPEC §7.2, §15).

All HTTP is mocked with respx. The whole point of these is that the client
degrades gracefully: a scan must never fail because this third-party endpoint
is slow, down, or returning something unexpected.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.carbon.green import API_BASE, check_green_hosting
from app.models import GreenSource


def _route(domain: str):
    return respx.get(f"{API_BASE}/{domain}")


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


@respx.mock
async def test_green_host_is_reported_as_green():
    _route("google.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "google.com",
                "green": True,
                "hosted_by": "Google Cloud",
                "hosted_by_website": "https://cloud.google.com",
            },
        )
    )

    result = await check_green_hosting("google.com")

    assert result.green is True
    assert result.hosted_by == "Google Cloud"
    assert result.source is GreenSource.GREENWEB
    assert result.host == "google.com"


@respx.mock
async def test_non_green_host_is_reported_from_the_api_not_as_unavailable():
    """green=false is a real answer and must not be confused with 'unknown'."""
    _route("example.invalid").mock(
        return_value=httpx.Response(200, json={"url": "example.invalid", "green": False})
    )

    result = await check_green_hosting("example.invalid")

    assert result.green is False
    assert result.source is GreenSource.GREENWEB
    assert result.hosted_by is None


@respx.mock
async def test_blank_hosted_by_becomes_none():
    _route("example.com").mock(
        return_value=httpx.Response(200, json={"green": True, "hosted_by": "   "})
    )
    result = await check_green_hosting("example.com")
    assert result.hosted_by is None


# --------------------------------------------------------------------------- #
# Failure paths — every one must degrade, not raise
# --------------------------------------------------------------------------- #


@respx.mock
async def test_timeout_degrades_to_unavailable():
    _route("slow.example").mock(side_effect=httpx.ConnectTimeout("too slow"))

    result = await check_green_hosting("slow.example")

    assert result.source is GreenSource.UNAVAILABLE
    assert result.green is False


@respx.mock
async def test_read_timeout_degrades_to_unavailable():
    _route("slow.example").mock(side_effect=httpx.ReadTimeout("too slow"))
    result = await check_green_hosting("slow.example")
    assert result.source is GreenSource.UNAVAILABLE


@respx.mock
async def test_connection_error_degrades_to_unavailable():
    _route("down.example").mock(side_effect=httpx.ConnectError("refused"))
    result = await check_green_hosting("down.example")
    assert result.source is GreenSource.UNAVAILABLE


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
@respx.mock
async def test_error_status_codes_degrade_to_unavailable(status: int):
    _route("example.com").mock(return_value=httpx.Response(status, json={}))
    result = await check_green_hosting("example.com")
    assert result.source is GreenSource.UNAVAILABLE
    assert result.green is False


@respx.mock
async def test_invalid_json_degrades_to_unavailable():
    _route("example.com").mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    result = await check_green_hosting("example.com")
    assert result.source is GreenSource.UNAVAILABLE


@respx.mock
async def test_json_array_degrades_to_unavailable():
    _route("example.com").mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    result = await check_green_hosting("example.com")
    assert result.source is GreenSource.UNAVAILABLE


@respx.mock
async def test_missing_green_field_degrades_to_unavailable():
    _route("example.com").mock(return_value=httpx.Response(200, json={"url": "example.com"}))
    result = await check_green_hosting("example.com")
    assert result.source is GreenSource.UNAVAILABLE


@respx.mock
async def test_non_boolean_green_field_degrades_to_unavailable():
    _route("example.com").mock(return_value=httpx.Response(200, json={"green": "yes"}))
    result = await check_green_hosting("example.com")
    assert result.source is GreenSource.UNAVAILABLE


# --------------------------------------------------------------------------- #
# Host normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("given", "queried"),
    [
        ("Example.COM", "example.com"),
        ("example.com.", "example.com"),
        ("example.com:8443", "example.com"),
        ("https://example.com/some/path", "example.com"),
        ("  example.com  ", "example.com"),
    ],
)
@respx.mock
async def test_host_is_normalised_before_the_lookup(given: str, queried: str):
    route = _route(queried).mock(return_value=httpx.Response(200, json={"green": True}))

    result = await check_green_hosting(given)

    assert route.called, f"expected a request to /{queried}"
    assert result.host == queried
    assert result.green is True


@pytest.mark.parametrize("given", ["", "   ", "://"])
async def test_empty_host_short_circuits_without_a_request(given: str):
    with respx.mock:
        result = await check_green_hosting(given)
    assert result.source is GreenSource.UNAVAILABLE


# --------------------------------------------------------------------------- #
# Client reuse
# --------------------------------------------------------------------------- #


@respx.mock
async def test_injected_client_is_used_and_left_open():
    _route("example.com").mock(return_value=httpx.Response(200, json={"green": True}))

    async with httpx.AsyncClient() as client:
        result = await check_green_hosting("example.com", client=client)
        assert result.green is True
        # The caller owns the client; we must not have closed it.
        assert not client.is_closed

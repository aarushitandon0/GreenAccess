"""SSRF-safe fetcher used by the patcher (CLAUDE.md security rules)."""

from __future__ import annotations

import pytest

from app.security.fetch import FetchError, SafeFetcher

LOCAL = ("127.0.0.1",)


async def test_fetches_an_allow_listed_local_host(local_site) -> None:
    base = local_site({"/a.css": (200, {"Content-Type": "text/css"}, b"body{}")})
    fetched = await SafeFetcher(allowed_local_hosts=LOCAL).get(f"{base}/a.css")
    assert (fetched.status, fetched.body, fetched.content_type) == (200, b"body{}", "text/css")


async def test_local_host_is_blocked_unless_allow_listed(local_site) -> None:
    base = local_site({"/": (200, {}, b"x")})
    with pytest.raises(FetchError) as caught:
        await SafeFetcher().get(f"{base}/")
    assert caught.value.blocked is True


@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "file:///etc/passwd"],
)
async def test_redirects_are_revalidated(local_site, target: str) -> None:
    base = local_site({"/go": (302, {"Location": target}, b"")})
    with pytest.raises(FetchError) as caught:
        await SafeFetcher(allowed_local_hosts=LOCAL).get(f"{base}/go")
    assert caught.value.blocked is True


async def test_redirect_within_allowed_hosts_is_followed(local_site) -> None:
    base = local_site({"/go": (301, {"Location": "/final"}, b""), "/final": (200, {}, b"ok")})
    fetched = await SafeFetcher(allowed_local_hosts=LOCAL).get(f"{base}/go")
    assert fetched.body == b"ok" and fetched.final_url.endswith("/final")


async def test_per_file_cap(local_site) -> None:
    base = local_site({"/big": (200, {}, b"x" * 5000)})
    with pytest.raises(FetchError, match="larger than"):
        await SafeFetcher(allowed_local_hosts=LOCAL, max_file_bytes=1000).get(f"{base}/big")


async def test_total_budget_across_files(local_site) -> None:
    base = local_site({"/a": (200, {}, b"x" * 600), "/b": (200, {}, b"y" * 600)})
    fetcher = SafeFetcher(allowed_local_hosts=LOCAL, max_total_bytes=1000)
    await fetcher.get(f"{base}/a")
    with pytest.raises(FetchError, match="budget"):
        await fetcher.get(f"{base}/b")


async def test_results_are_memoised(local_site) -> None:
    base = local_site({"/a": (200, {}, b"x" * 600)})
    fetcher = SafeFetcher(allowed_local_hosts=LOCAL, max_total_bytes=1000)
    first = await fetcher.get(f"{base}/a")
    assert await fetcher.get(f"{base}/a") is first
    assert fetcher.fetched_bytes == 600

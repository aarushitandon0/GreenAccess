"""Green Web Foundation hosting lookup (MASTERSPEC §7.2).

Asks the Green Web Foundation whether a host runs on renewable energy. The
answer feeds two things: the data-centre segment of the carbon calculation uses
a lower grid intensity when the host is green (see :mod:`app.carbon.swd`), and
scoring adds a small bonus (MASTERSPEC §9.2).

This is a third-party network call in the middle of a scan, so it is built to
fail quietly: any error, timeout or unexpected payload yields
``source="unavailable"`` and ``green=False``, and the UI shows that as
"unknown" rather than as "not green". A scan never fails because this endpoint
is down.

API: https://api.thegreenwebfoundation.org/api/v3/greencheck/{domain}
"""

from __future__ import annotations

import logging

import httpx

from app.models import GreenResult, GreenSource

logger = logging.getLogger(__name__)

API_BASE = "https://api.thegreenwebfoundation.org/api/v3/greencheck"

# MASTERSPEC §7.2 fixes this at 5 seconds.
TIMEOUT_S = 5.0

USER_AGENT = "GreenAccessBot/0.1 (+https://github.com/greenaccess)"


def _unavailable(host: str) -> GreenResult:
    return GreenResult(host=host, green=False, source=GreenSource.UNAVAILABLE)


def _parse(host: str, payload: object) -> GreenResult:
    """Turn an API payload into a GreenResult, tolerating anything unexpected."""
    if not isinstance(payload, dict):
        logger.warning("greencheck for %s returned %s, not an object", host, type(payload).__name__)
        return _unavailable(host)

    green = payload.get("green")
    if not isinstance(green, bool):
        # The API returns green=false for unknown hosts too, but a missing or
        # non-boolean field means we did not understand the response at all.
        logger.warning("greencheck for %s had no boolean 'green' field", host)
        return _unavailable(host)

    hosted_by = payload.get("hosted_by")
    if not isinstance(hosted_by, str) or not hosted_by.strip():
        hosted_by = None

    return GreenResult(
        host=host,
        green=green,
        hosted_by=hosted_by,
        source=GreenSource.GREENWEB,
    )


async def check_green_hosting(
    host: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = TIMEOUT_S,
) -> GreenResult:
    """Look up `host`. Never raises.

    Pass `client` to reuse a connection pool, or to inject a mock in tests.
    """
    cleaned = (host or "").strip().lower().rstrip(".")
    if not cleaned:
        return _unavailable(host or "")

    # The API wants a bare domain, not a URL or a host:port pair.
    if "//" in cleaned:
        cleaned = cleaned.split("//", 1)[1]
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0]
    if not cleaned:
        return _unavailable(host)

    url = f"{API_BASE}/{cleaned}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
    try:
        response = await http.get(url, headers=headers, timeout=timeout_s)
        if response.status_code != 200:
            logger.info("greencheck for %s returned HTTP %s", cleaned, response.status_code)
            return _unavailable(cleaned)
        return _parse(cleaned, response.json())
    except httpx.TimeoutException:
        logger.info("greencheck for %s timed out after %ss", cleaned, timeout_s)
        return _unavailable(cleaned)
    except httpx.HTTPError as exc:
        logger.info("greencheck for %s failed: %s", cleaned, exc)
        return _unavailable(cleaned)
    except ValueError as exc:
        # Includes JSON decode errors.
        logger.info("greencheck for %s returned unparseable JSON: %s", cleaned, exc)
        return _unavailable(cleaned)
    finally:
        if owns_client:
            await http.aclose()

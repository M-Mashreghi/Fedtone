"""Helpers for FOMC fixture-backed tests."""

from pathlib import Path

import httpx

SYNTHETIC = Path(__file__).parent / "fixtures" / "fomc" / "synthetic"
LIVE = Path(__file__).parent / "fixtures" / "fomc" / "live"
BASE = "https://www.federalreserve.gov"

SYNTHETIC_ROUTES = {
    f"{BASE}/monetarypolicy/fomccalendars.htm": "calendar.html",
    f"{BASE}/newsevents/pressreleases/monetary20231213a.htm": "statement_20231213.html",
    f"{BASE}/newsevents/pressreleases/monetary20240131a.htm": "statement_20240131.html",
    f"{BASE}/newsevents/pressreleases/monetary20240320a.htm": "statement_20240320.html",
}


def read(name: str) -> str:
    return (SYNTHETIC / name).read_text(encoding="utf-8")


def fixture_transport(routes: dict[str, str] | None = None, overrides=None) -> httpx.MockTransport:
    """Serves synthetic fixture files by URL; ``overrides`` maps URL -> httpx.Response."""
    routes = SYNTHETIC_ROUTES if routes is None else routes
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in overrides:
            return overrides[url]
        if url in routes:
            return httpx.Response(
                200,
                content=(SYNTHETIC / routes[url]).read_bytes(),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)

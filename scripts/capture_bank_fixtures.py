"""Save official ECB, Bank of England and SNB pages as test fixtures.

Run from the project root on a machine that can reach the official sites:

    python scripts/capture_bank_fixtures.py            # all three banks
    python scripts/capture_bank_fixtures.py ecb snb    # selected banks

Files are saved byte-for-byte under tests/fixtures/<bank>/live/ and are then exercised by
tests/unit/test_bank_live_fixtures.py. Review the files (and each site's reuse terms) before
committing them.
"""

import sys
from pathlib import Path

import httpx

from fedtone.infrastructure.sources import boe, ecb, snb

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
USER_AGENT = "FedTone research fixture capture"
ECB_YEAR = 2024
MAX_DOCUMENTS = 3


def capture_ecb(client: httpx.Client, out: Path) -> None:
    listing = client.get(ecb.yearly_listing_url(ECB_YEAR))
    (out / "listing.html").write_bytes(listing.content)
    for url in ecb.discover_decision_links(listing.text, str(listing.url))[:MAX_DOCUMENTS]:
        day = ecb.decision_date_from_url(url)
        _save(client, url, out / f"decision_{day:%Y%m%d}.html")


def capture_boe(client: httpx.Client, out: Path) -> None:
    listing = client.get(boe.DEFAULT_LISTING_URLS[0])
    (out / "listing.xml").write_bytes(listing.content)
    for url in boe.discover_summary_links(listing.text, str(listing.url))[:MAX_DOCUMENTS]:
        year, month = boe.summary_month_from_url(url)
        _save(client, url, out / f"summary_{year}{month:02d}.html")


def capture_snb(client: httpx.Client, out: Path) -> None:
    listing = client.get(snb.DEFAULT_LISTING_URLS[0])
    (out / "listing.html").write_bytes(listing.content)
    for url in snb.discover_press_release_links(listing.text, str(listing.url))[:MAX_DOCUMENTS]:
        page = client.get(url)
        pdf_url = snb.find_pdf_link(page.text, str(page.url))
        day = snb.release_date_from_url(url)
        _save(client, pdf_url, out / f"assessment_{day:%Y%m%d}.pdf")


def _save(client: httpx.Client, url: str, path: Path) -> None:
    response = client.get(url)
    if response.status_code != 200:
        print(f"skip {url}: HTTP {response.status_code}")
        return
    path.write_bytes(response.content)
    print(f"saved {path.relative_to(ROOT)} ({len(response.content)} bytes) from {url}")


CAPTURES = {"ecb": capture_ecb, "boe": capture_boe, "snb": capture_snb}


def main(banks: list[str]) -> int:
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        for bank in banks or list(CAPTURES):
            out = ROOT / bank / "live"
            out.mkdir(parents=True, exist_ok=True)
            CAPTURES[bank](client, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

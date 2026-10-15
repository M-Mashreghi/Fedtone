"""Save official FOMC pages as test fixtures.

Run from the project root on a machine that can reach federalreserve.gov:

    python scripts/capture_fomc_fixtures.py                 # calendar + a default set
    python scripts/capture_fomc_fixtures.py 20240131 20231213

Pages are saved byte-for-byte under tests/fixtures/fomc/live/ and are then exercised by
tests/unit/test_fomc_live_fixtures.py. Review the files before committing them.
"""

import sys
from pathlib import Path

import httpx

BASE = "https://www.federalreserve.gov"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fomc" / "live"
DEFAULT_DATES = ("20231213", "20240131", "20240320", "20240918", "20241218")
USER_AGENT = "FedTone research fixture capture"


def main(dates: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    targets = {"calendar.html": f"{BASE}/monetarypolicy/fomccalendars.htm"}
    for day in dates or DEFAULT_DATES:
        targets[f"statement_{day}.html"] = f"{BASE}/newsevents/pressreleases/monetary{day}a.htm"
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        for name, url in targets.items():
            response = client.get(url)
            if response.status_code != 200:
                print(f"skip {url}: HTTP {response.status_code}")
                continue
            (OUT / name).write_bytes(response.content)
            print(f"saved {name} ({len(response.content)} bytes) from {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

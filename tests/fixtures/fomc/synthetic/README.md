# Synthetic FOMC fixtures

These HTML files are **synthetic**. They imitate the layout of federalreserve.gov press-release
and calendar pages (`#article`, `.article__time`, `.title`, `.releaseTime`, trailing
implementation-note and media-contact lines), but the statement wording is invented test text,
not official Federal Reserve language.

They exist because the environment that built Phase 2 could not reach federalreserve.gov.
Captured official snapshots belong in `tests/fixtures/fomc/live/`
(see `scripts/capture_fomc_fixtures.py`); `tests/unit/test_fomc_live_fixtures.py` runs the
parser against every file found there.

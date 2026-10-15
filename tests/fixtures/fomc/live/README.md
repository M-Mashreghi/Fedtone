# Captured official FOMC pages

Place byte-for-byte snapshots of federalreserve.gov pages here, named
`statement_YYYYMMDD.html` and `calendar.html`. Create them with:

    python scripts/capture_fomc_fixtures.py

`tests/unit/test_fomc_live_fixtures.py` parses every file in this folder; it is skipped when
the folder has no snapshots.

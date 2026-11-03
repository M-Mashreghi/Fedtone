# Synthetic ECB, BoE and SNB fixtures

The HTML/XML files in `tests/fixtures/{ecb,boe,snb}/synthetic/` imitate the layout of the
official pages (ECB press release and yearly decision list, Bank of England Monetary Policy
Summary page and news RSS feed, SNB decisions page and press-release page). All wording is
invented. SNB assessment PDFs are generated at test time by `tests/bank_fixtures.py` with
`tests/pdf_fixture.py`. Real pages go to the `live/` folders.

from datetime import UTC, datetime, timedelta

from fedtone.domain.linking import previous_comparable_links
from fedtone.domain.models import PolicyDocument

T0 = datetime(2023, 12, 13, 19, tzinfo=UTC)


def doc(day_offset: int | None, url: str, bank="FED", doc_type="fomc_statement") -> PolicyDocument:
    published = None if day_offset is None else T0 + timedelta(days=day_offset)
    return PolicyDocument(bank, doc_type, url, discovered_at=T0, published_at=published)


def test_links_follow_publication_order_not_input_order() -> None:
    dec, jan, mar = doc(0, "dec"), doc(49, "jan"), doc(98, "mar")

    links = previous_comparable_links([mar, dec, jan])

    assert links == {dec.id: None, jan.id: dec.id, mar.id: jan.id}


def test_backfilled_earlier_document_relinks_successor() -> None:
    jan, mar = doc(49, "jan"), doc(98, "mar")
    assert previous_comparable_links([jan, mar])[jan.id] is None

    dec = doc(0, "dec")
    links = previous_comparable_links([jan, mar, dec])

    assert links[jan.id] == dec.id


def test_no_look_ahead_previous_is_always_strictly_earlier() -> None:
    docs = [doc(offset, f"d{offset}") for offset in (40, 0, 90, 10, 60)]
    by_id = {d.id: d for d in docs}

    for doc_id, previous_id in previous_comparable_links(docs).items():
        if previous_id is not None:
            assert by_id[previous_id].published_at < by_id[doc_id].published_at


def test_same_timestamp_documents_do_not_link_to_each_other() -> None:
    first, a, b = doc(0, "first"), doc(5, "a"), doc(5, "b")

    links = previous_comparable_links([first, a, b])

    assert links[a.id] == first.id
    assert links[b.id] == first.id


def test_groups_by_bank_and_type_and_skips_undated_documents() -> None:
    fed, ecb, minutes, undated = (
        doc(0, "f"),
        doc(5, "e", bank="ECB"),
        doc(7, "m", doc_type="minutes"),
        doc(None, "u"),
    )
    later_fed = doc(10, "f2")

    links = previous_comparable_links([fed, ecb, minutes, undated, later_fed])

    assert links[later_fed.id] == fed.id
    assert links[ecb.id] is None
    assert links[minutes.id] is None
    assert undated.id not in links

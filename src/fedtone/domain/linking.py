"""Previous-comparable-document linking.

A document's previous comparable document is the latest document of the same central bank
and document type published strictly before it. Only documents with a known publication time
take part, so the link never depends on information published later (no look-ahead).
"""

from collections.abc import Iterable
from uuid import UUID

from fedtone.domain.models import PolicyDocument


def previous_comparable_links(documents: Iterable[PolicyDocument]) -> dict[UUID, UUID | None]:
    """Map each dated document id to its previous comparable document id (None = baseline)."""
    groups: dict[tuple[str, str], list[PolicyDocument]] = {}
    for doc in documents:
        if doc.published_at is not None:
            groups.setdefault((doc.central_bank, doc.document_type), []).append(doc)

    links: dict[UUID, UUID | None] = {}
    for group in groups.values():
        group.sort(key=lambda d: (d.published_at, d.source_url))
        for index, doc in enumerate(group):
            # Strictly earlier: same-timestamp documents never link to each other.
            earlier = index - 1
            while earlier >= 0 and group[earlier].published_at == doc.published_at:
                earlier -= 1
            links[doc.id] = group[earlier].id if earlier >= 0 else None
    return links

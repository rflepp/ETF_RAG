from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..net import Client
from ..schema import DocType, Fund


@dataclass
class DocLink:
    doc_type: DocType
    url: str


@dataclass
class Listing:
    """One fund as seen in a provider's product list, before any document is read."""
    fund: Fund                      # pre-filled with facts from the provider's own API
    raw: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def list_funds(self, client: Client) -> list[Listing]: ...

    def doc_links(self, client: Client, listing: Listing) -> list[DocLink]: ...

    def apply_docs(self, client: Client, listing: Listing, docs: dict, pages: dict) -> None:
        """Fill provider-specific facts from downloaded docs (factsheet layout, holdings files)."""
        ...

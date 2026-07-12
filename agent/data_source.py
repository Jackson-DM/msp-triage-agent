"""Data-source adapter: tickets reach the agent and runner only through
this interface. The live Freshdesk adapter slots in here in week 2 —
nothing outside this module may assume tickets come from a JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class DataSource(Protocol):
    def load_tickets(self) -> list[dict]: ...


class LocalJSONDataSource:
    """Serves the synthetic golden suite from a JSON file on disk."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def load_tickets(self) -> list[dict]:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return data["tickets"]

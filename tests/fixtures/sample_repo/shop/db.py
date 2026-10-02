"""Tiny in-memory database used by the services layer."""

from itertools import count


class Database:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self._ids = count(1)

    def insert(self, table: str, row: dict) -> int:
        row = {"id": next(self._ids), **row}
        self.tables.setdefault(table, []).append(row)
        return row["id"]

    def find_one(self, table: str, **filters) -> dict | None:
        for row in self.tables.get(table, []):
            if all(row.get(k) == v for k, v in filters.items()):
                return row
        return None

"""scripts/rename_board_columns.py: rename Status options, no card loses its column."""

import importlib.util
from pathlib import Path

import pytest

from agentflow import board as b
from agentflow.board import Card

_spec = importlib.util.spec_from_file_location(
    "rename_board_columns", Path(__file__).parent.parent / "scripts/rename_board_columns.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


class FakeBoard:
    """The slice of Board the script uses, with the platform's semantics for option ids: an
    option sent back with its id keeps its cards; `keeps_ids=False` models a field update
    that drops them, so the snapshot has to restore the statuses."""

    def __init__(self, keeps_ids=True):
        self.keeps_ids = keeps_ids
        self.options = [{"id": f"id{i}", "name": n, "color": "GRAY", "description": ""}
                        for i, n in enumerate(b.LEGACY_COLUMNS)]  # fmt: skip
        self.item_option = {"A": "id0", "B": "id2", "C": "id6", "D": None}
        self.sets: list[tuple[str, str]] = []
        self.replaced = 0

    def _name(self, oid):
        return next((o["name"] for o in self.options if o["id"] == oid), None)

    async def status_options(self):
        return [dict(o) for o in self.options]

    async def replace_status_options(self, options):
        self.replaced += 1
        old = {o["id"] for o in self.options}
        new = []
        for i, o in enumerate(options):
            kept = self.keeps_ids and o.get("id") in old
            new.append({"id": o["id"] if kept else f"new{self.replaced}-{i}", "name": o["name"], "color": o["color"],
                        "description": o["description"]})  # fmt: skip
        gone = old - {o["id"] for o in new}
        self.item_option = {k: (None if v in gone else v) for k, v in self.item_option.items()}
        self.options = new

    async def cards(self, include_closed=False):
        return [Card(item_id=k, issue_id=k, number=1, repo="o/r", url="u", title=k, status=b.canonical(self._name(v)))
                for k, v in self.item_option.items()]  # fmt: skip

    async def set_field(self, card, field, value):
        assert field == "Status"
        self.sets.append((card.item_id, value))
        self.item_option[card.item_id] = next(o["id"] for o in self.options if o["name"] == value)


def test_plan_keeps_ids_and_adds_missing_columns():
    current = [
        {"id": "x", "name": "Бэклог", "color": "GRAY", "description": "d"},
        {"id": "y", "name": "Todo", "color": "RED", "description": ""},
    ]
    out = script.plan_options(current)
    assert out[0] == {"id": "x", "name": "Backlog", "color": "GRAY", "description": "d"}
    assert [o["name"] for o in out[:7]] == list(b.COLUMNS) and "id" not in out[1]
    assert out[-1]["name"] == "Todo" and out[-1]["id"] == "y"  # an option we do not know is left alone


async def test_rename_with_ids_kept_changes_names_and_restores_nothing():
    fb = FakeBoard()
    lines = await script.rename(fb)
    assert [o["name"] for o in fb.options] == list(b.COLUMNS)
    assert fb.sets == [] and "snapshot: 0" in lines[-1]
    assert [c.status for c in await fb.cards()] == [b.BACKLOG, b.DOING, b.BLOCKED, None]
    assert len(lines) <= 10


async def test_rename_restores_statuses_when_the_options_lose_their_cards():
    fb = FakeBoard(keeps_ids=False)
    lines = await script.rename(fb)
    assert [c.status for c in await fb.cards()] == [b.BACKLOG, b.DOING, b.BLOCKED, None]  # D had none, gets none
    assert sorted(fb.sets) == [("A", b.BACKLOG), ("B", b.DOING), ("C", b.BLOCKED)] and "snapshot: 3" in lines[-1]


async def test_dry_run_changes_nothing_and_a_second_run_is_a_no_op():
    fb = FakeBoard()
    lines = await script.rename(fb, dry_run=True)
    assert fb.replaced == 0 and fb.options[0]["name"] == "Бэклог" and "dry run" in lines[-1]
    await script.rename(fb)
    again = await script.rename(fb)
    assert fb.replaced == 1 and "nothing to do" in again[-1]


@pytest.mark.parametrize("keeps", [True, False])
async def test_cards_hold_their_status_either_way(keeps):
    fb = FakeBoard(keeps_ids=keeps)
    before = [c.status for c in await fb.cards()]
    await script.rename(fb)
    assert [c.status for c in await fb.cards()] == before

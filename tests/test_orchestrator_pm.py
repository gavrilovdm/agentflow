import pytest

from agentflow import board as b
from agentflow import orchestrator_pm as pm
from agentflow.board import Card, Comment, Thread


def _card(n, status, owner=None, repo=None, priority="normal", updated="t1", **kw):
    return Card(item_id=f"IT{n}", issue_id=f"I{n}", number=n, repo=repo or f"o/r{n}", url=f"u{n}", title=f"c{n}",
                status=status, owner=owner, priority=priority, updated_at=updated, **kw)  # fmt: skip


def test_fifth_card_waits_while_four_are_in_doing():
    working = [_card(i, b.DOING, owner="developer") for i in range(1, 5)]
    d = pm.decide([*working, _card(5, b.DOING)], {c.item_id for c in working}, {}, 0, None)
    assert d.launch == [] and any("WIP 4" in h for h in d.held)
    d = pm.decide([*working[:3], _card(5, b.DOING)], {c.item_id for c in working[:3]}, {}, 0, None)
    assert [(a, c.number) for a, c in d.launch] == [("developer", 5)]


def test_free_cards_go_by_priority_and_one_per_repo_in_doing():
    cards = [
        _card(1, b.DOING, repo="o/a"),
        _card(2, b.DOING, repo="o/a", priority="high"),
        _card(3, b.DOING, repo="o/b"),
    ]
    d = pm.decide(cards, set(), {}, 0, None)
    assert [c.number for _, c in d.launch] == [2, 3]  # 1 waits: o/a already gets a card
    assert any("o/a already" in h for h in d.held)


def test_an_owner_without_a_heartbeat_is_stuck():
    d = pm.decide([_card(1, b.TESTING, owner="tester"), _card(2, b.TESTING, owner="Daniil")], set(), {}, 0, None)
    assert [c.number for c in d.stuck] == [1]  # a person's name in Owner is theirs to keep


def test_open_breaker_starts_nothing_and_backlog_waits_for_people():
    cards = [_card(1, b.BACKLOG), _card(2, b.PLANNING)]
    assert pm.decide(cards, set(), {}, 0, "credit balance").launch == []
    d = pm.decide(cards, set(), {}, 4, None)
    assert [(a, c.number) for a, c in d.launch] == [("planner", 2)]


def test_a_card_waiting_for_words_is_skipped_until_its_issue_changes():
    card = _card(1, b.PLANNING, updated="t1")
    assert pm.decide([card], set(), {"IT1": "t1"}, 0, None).launch == []
    assert len(pm.decide([card.model_copy(update={"updated_at": "t2"})], set(), {"IT1": "t1"}, 0, None).launch) == 1


def test_approval_mode():
    assert pm.approval_mode(_card(1, b.PLANNING, type="research"), 5) == "none"
    assert pm.approval_mode(_card(1, b.PLANNING, type="coding"), 2) == "plan_only"
    assert pm.approval_mode(_card(1, b.PLANNING, type="coding", priority="high"), 2) == "full"
    assert pm.approval_mode(_card(1, b.PLANNING, type="coding"), 3) == "full"


def test_waiting_on_human_counts_blocked_plans_and_merges():
    plan = Thread(
        title="t", body="", author="u", comments=[Comment(author="b", body="<!-- agentflow:plan v -->", created_at="1")]
    )
    ask = Thread(
        title="t",
        body="",
        author="u",
        comments=[Comment(author="b", body="<!-- agentflow:merge-ask -->", created_at="1")],
    )
    cards = [_card(1, b.BLOCKED), _card(2, b.PLANNING), _card(3, b.READY), _card(4, b.PLANNING)]
    assert pm.waiting_on_human(cards, {"IT2": plan, "IT3": ask, "IT4": Thread(title="t", body="", author="u")}) == 3


class FakeRedis:
    def __init__(self):
        self.kv = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, key):
        self.kv.pop(key, None)


class FakeBoard:
    def __init__(self, cards, comments=()):
        self._cards = cards
        self.comments = list(comments)
        self.calls = []

    async def cards(self, status=None, include_closed=False):
        return self._cards

    async def thread(self, card):
        return Thread(title="t", body="## Definition of Done\n- [ ] a\n", author="u", comments=self.comments)

    async def set_field(self, card, field, value):
        self.calls.append(("set_field", card.number, field, value))

    async def comment(self, card, body):
        self.calls.append(("comment", card.number, body))
        self.comments.append(Comment(author="bot", body=body, created_at="x"))

    async def move(self, card, to, report):
        self.calls.append(("move", card.number, to))


async def test_tick_hands_the_card_out_before_queueing_and_frees_the_stuck_once_then_blocks():
    redis, queued = FakeRedis(), []

    async def enqueue(agent, item_id):
        assert redis.kv[pm.CARD_PREFIX + item_id] == "queued"  # a queued job is not mistaken for a dead one
        queued.append((agent, item_id))

    fb = FakeBoard([_card(1, b.PLANNING), _card(2, b.DOING, owner="developer")])
    d = await pm.tick(fb, redis, enqueue)
    assert queued == [("planner", "IT1")]
    assert ("set_field", 1, "Approval", "plan_only") in fb.calls and ("set_field", 1, "Owner", "planner") in fb.calls
    assert ("set_field", 2, "Owner", None) in fb.calls and [c.number for c in d.stuck] == [2]

    fb.calls.clear()
    fb._cards = [_card(2, b.DOING, owner="developer")]
    await pm.tick(fb, redis, enqueue)
    assert ("move", 2, b.BLOCKED) in fb.calls  # second disappearance: a person looks


async def test_provider_error_no_retry_fixes_opens_the_breaker(monkeypatch):
    redis = FakeRedis()

    async def broke(board, card):
        raise RuntimeError("Error code: 400 - Your credit balance is too low")

    monkeypatch.setattr(pm.planner, "process", broke)
    fb = FakeBoard([_card(1, b.PLANNING, owner="planner")])
    with pytest.raises(RuntimeError):
        await pm.work(fb, redis, "planner", "IT1")
    assert "credit balance" in redis.kv[pm.BREAKER_KEY]
    assert ("set_field", 1, "Owner", None) in fb.calls and pm.CARD_PREFIX + "IT1" not in redis.kv
    assert pm.decide([_card(2, b.BACKLOG)], set(), {}, 0, redis.kv[pm.BREAKER_KEY]).launch == []


async def test_waiting_outcome_remembers_the_issue_version(monkeypatch):
    redis = FakeRedis()

    async def waits(board, card):
        return pm.planner.Outcome("wait")

    monkeypatch.setattr(pm.planner, "process", waits)
    fb = FakeBoard([_card(1, b.PLANNING, owner="planner", updated="t7")])
    assert await pm.work(fb, redis, "planner", "IT1") == "wait"
    assert redis.kv[pm.SEEN_PREFIX + "IT1"] == "t7"
    assert await pm.work(fb, redis, "tester", "IT1") == "gone"  # not the agent it was handed to

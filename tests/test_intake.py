from agentflow import board as b
from agentflow.board import Card, Comment, Thread
from agentflow.stages import intake
from agentflow.stages.intake import IntakeDraft, judge

WL = {"o/r"}


def _card(title="Add counter", item="IT1", repo="o/r", status=b.BACKLOG):
    return Card(item_id=item, issue_id="I" + item, number=1, repo=repo, url=f"https://x/{item}", title=title,
                status=status)  # fmt: skip


def _draft(**kw):
    base = dict(type="coding", type_confidence=0.95, title="Add counter", goal="A counter function.",
                definition_of_done=["make_counter() returns 0, 1, 2 on three calls"])  # fmt: skip
    return IntakeDraft(**(base | kw))


def test_complete_card_is_ready():
    assert judge(_draft(), _card(), [], WL).kind == "ready"


def test_out_of_scope_repo_outside_whitelist_and_duplicate_are_rejected_with_a_reason():
    assert judge(_draft(type="out_of_scope"), _card(), [], WL).kind == "reject"
    v = judge(_draft(), _card(repo="evil/r"), [], WL)
    assert v.kind == "reject" and "whitelist" in v.reasons[0]
    other = _card(title="add  COUNTER!", item="IT2")
    v = judge(_draft(), _card(), [other], WL)
    assert v.kind == "reject" and other.url in v.reasons[0]
    # a merged card with the same title is history, not a duplicate
    assert judge(_draft(), _card(), [other.model_copy(update={"status": b.MERGED})], WL).kind == "ready"


def test_missing_or_vague_parts_become_questions_not_guesses():
    v = judge(_draft(type_confidence=0.5, goal="", definition_of_done=[]), _card(), [], WL)
    assert v.kind == "ask" and len(v.questions) == 3
    v = judge(_draft(definition_of_done=["works fast"], untestable=["works fast"]), _card(), [], WL)
    assert v.kind == "ask" and "works fast" in v.questions[0]


class FakeBoard:
    def __init__(self, thread):
        self._thread = thread
        self.calls = []

    async def thread(self, card):
        return self._thread

    async def update_issue(self, card, title, body):
        self.calls.append(("update_issue", title, body))

    async def set_field(self, card, field, value):
        self.calls.append(("set_field", field, value))

    async def move(self, card, to, report):
        self.calls.append(("move", to, report.summary))

    async def comment(self, card, body):
        self.calls.append(("comment", body))


def _extractor(draft):
    async def run(thread):
        run.seen = intake.render_thread(thread)
        return draft

    return run


async def test_ready_card_is_rewritten_to_the_template_and_moved_with_its_markers_kept():
    body = "make a counter\n\n<!-- agentflow:card source=telegram reply_to=chat:1/msg:2 -->"
    fb = FakeBoard(Thread(title="counter", body=body, author="u"))
    v = await intake.process(fb, _card(), [], WL, _extractor(_draft()))
    assert v.kind == "ready"
    _, title, new_body = fb.calls[0]
    assert title == "Add counter" and "- [ ] make_counter()" in new_body
    assert "source=telegram reply_to=chat:1/msg:2" in new_body  # the reply address survives
    assert ("set_field", "Task type", "coding") in fb.calls and fb.calls[-1][:2] == ("move", b.PLANNING)


async def test_questions_go_to_the_author_and_the_card_waits_for_an_answer():
    fb = FakeBoard(Thread(title="t", body="do something", author="u"))
    v = await intake.process(fb, _card(), [], WL, _extractor(_draft(goal="")))
    assert v.kind == "ask"
    comment = next(c for c in fb.calls if c[0] == "comment")[1]
    assert "Что должно измениться" in comment and "agentflow:questions" in comment and "round=1" in comment
    assert ("set_field", "Owner", None) in fb.calls and not any(c[0] == "move" for c in fb.calls)

    # nothing new from the author: no second call to the model, no second comment
    waiting = Thread(title="t", body="x", author="u", comments=[Comment(author="bot", body=comment, created_at="1")])
    fb2 = FakeBoard(waiting)
    assert (await intake.process(fb2, _card(), [], WL, _extractor(_draft()))).kind == "wait" and fb2.calls == []

    # the author answered: the answer reaches the model
    answered = waiting.model_copy(update={"comments": [*waiting.comments,
                                                      Comment(author="u", body="return 0, 1, 2", created_at="2")]})  # fmt: skip
    ext = _extractor(_draft())
    assert (await intake.process(FakeBoard(answered), _card(), [], WL, ext)).kind == "ready"
    assert "return 0, 1, 2" in ext.seen and "agentflow (questions)" in ext.seen


async def test_third_unanswered_round_blocks_instead_of_asking_forever():
    asked = [
        Comment(author="bot", body=f"q <!-- agentflow:questions round={i} -->", created_at=str(i)) for i in (1, 2, 3)
    ]
    answers = [Comment(author="u", body="hm", created_at="9")]
    fb = FakeBoard(Thread(title="t", body="x", author="u", comments=asked + answers))
    v = await intake.process(fb, _card(), [], WL, _extractor(_draft(goal="")))
    assert v.kind == "ask" and fb.calls[-1][:2] == ("move", b.BLOCKED)
    assert not any(c[0] == "comment" for c in fb.calls)

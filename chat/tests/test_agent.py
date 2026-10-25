import json

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from agentflow_chat.agent import ChatSession, tool_json
from agentflow_chat.config import Settings

CTX = ToolPermissionContext(tool_use_id="t1")


class FakeConfirmer:
    def __init__(self, answer):
        self.answer = answer
        self.asked: list[str] = []

    async def confirm(self, chat_id, question):
        self.asked.append(question)
        return self.answer


def _session(answer=(True, None)) -> tuple[ChatSession, FakeConfirmer]:
    c = FakeConfirmer(answer)
    return ChatSession(1, c, Settings()), c


async def test_reads_and_user_driven_actions_need_no_confirmation():
    s, c = _session()
    for tool in ("mcp__agentflow__find_repos", "mcp__agentflow__review_checkpoint"):
        assert isinstance(await s.can_use_tool(tool, {}, CTX), PermissionResultAllow)
    assert not c.asked


async def test_other_tools_and_repo_less_runs_are_denied():
    s, _ = _session()
    assert isinstance(await s.can_use_tool("Bash", {"command": "ls"}, CTX), PermissionResultDeny)
    assert isinstance(await s.can_use_tool("mcp__agentflow__start_run", {"prompt": "x"}, CTX), PermissionResultDeny)


async def test_start_run_asks_and_passes_the_users_objection_back():
    s, c = _session(answer=(False, "нет, в octo/web"))
    s.state.last_user_text = "почини логин в мобильном бэкенде"
    r = await s.can_use_tool("mcp__agentflow__start_run", {"prompt": "fix login", "repo": "octo/api"}, CTX)
    assert isinstance(r, PermissionResultDeny) and "нет, в octo/web" in r.message
    assert c.asked and "octo/api" in c.asked[0]


async def test_start_run_skips_question_when_user_named_repo():
    s, c = _session()
    s.state.last_user_text = "в octo/api почини логин"
    r = await s.can_use_tool("mcp__agentflow__start_run", {"prompt": "fix login", "repo": "octo/api"}, CTX)
    assert isinstance(r, PermissionResultAllow) and not c.asked


async def test_list_repos_hook_records_the_whitelist():
    s, _ = _session()
    payload = [{"full_name": "octo/api", "description": "x"}]
    await s._on_list_repos({"tool_response": {"content": [{"type": "text", "text": json.dumps(payload)}]}}, None, None)
    assert s.state.whitelist == ["octo/api"]


def test_tool_json_shapes():
    items = [{"full_name": "a/b"}, {"full_name": "c/d"}]
    assert tool_json(json.dumps(items)) == items
    assert tool_json({"structuredContent": {"result": items}}) == items
    # one text block per list item
    assert tool_json([{"type": "text", "text": json.dumps(i)} for i in items]) == items
    assert tool_json(items) == items

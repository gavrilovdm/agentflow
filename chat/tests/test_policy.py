from agentflow_chat.policy import ChatState, is_yes, needs_confirmation, repo_named, with_reply_context


def test_repo_named_by_full_or_bare_name():
    assert repo_named("octo/mobile-api", "почини логин в octo/mobile-api")
    assert repo_named("octo/mobile-api", "почини логин в mobile-api, пожалуйста")
    # "api" alone must not count as naming "octo/api-gateway", nor "mobile-api-v2" as "mobile-api".
    assert not repo_named("octo/api-gateway", "почини api логина")
    assert not repo_named("octo/mobile-api", "это в mobile-api-v2")


def test_confirmation_needed_unless_named_or_sole_candidate():
    s = ChatState(last_user_text="почини логин в мобильном бэкенде")
    assert needs_confirmation("octo/mobile-api", s)
    s.whitelist = ["octo/mobile-api", "octo/web"]
    assert needs_confirmation("octo/mobile-api", s)
    s.whitelist = ["Octo/Mobile-API"]
    assert not needs_confirmation("octo/mobile-api", s)
    assert not needs_confirmation("octo/web", ChatState(last_user_text="в octo/web добавь X"))
    assert needs_confirmation(None, s)


def test_is_yes():
    assert is_yes("Да") and is_yes("ok!") and is_yes("👍")
    assert not is_yes("нет, в web") and not is_yes("да, но в другом")


def test_reply_context_carries_the_thread_id():
    note = "🟡 *Approval needed* — spec\n\n1. Add X\n\nthread: `mcp-abc123`"
    out = with_reply_context("убери пункт 3", note)
    assert "прогон mcp-abc123" in out and out.endswith("убери пункт 3")
    assert with_reply_context("привет", None) == "привет"

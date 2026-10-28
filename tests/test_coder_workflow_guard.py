from agentflow.agents.coder import build_file_tools


def test_coder_cannot_write_github_workflows(tmp_path):
    """The push token lacks `workflow` scope: one ci.yml made GitHub reject the whole branch."""
    written: dict[str, str] = {}
    write = next(t for t in build_file_tools(tmp_path, written) if t.name == "write_file")
    out = write.invoke({"file_path": ".github/workflows/ci.yml", "content": "on: push"})
    assert out.startswith("Refusing")
    assert not (tmp_path / ".github").exists()
    assert written == {}
    assert write.invoke({"file_path": "README.md", "content": "x"}) == "Written: README.md"

# agentflow-chat

A conversational layer over agentflow. You write to a Telegram bot in plain language. It picks the repository, starts the run, and answers questions about it. It is built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk), and its only tools are agentflow's MCP server. It doesn't import agentflow, so it can be replaced by Claude Code, Claude Desktop or another front end connected to the same MCP server.

```
Telegram ─▶ agentflow-chat (Claude Agent SDK) ─▶ agentflow MCP ─▶ queue ─▶ worker
Claude Code / Desktop ──────────────────────────▶ agentflow MCP ─▶ queue ─▶ worker
agentflow's own bot ◀── spec/plan approvals, failed-task dossiers, PR links (unchanged)
```

Notifications still come from agentflow's own bot, not from this layer. They arrive even when the layer is down, and the layer never polls in the background: it only answers your messages. To act on a notification in words ("в спеке убери пункт 3"), reply to it. The bridge passes the notification text and its `thread` id to the model, which then calls `review_checkpoint` or `resolve_failed_task`.

## Picking a repository

Runs may only target repos in agentflow's whitelist (`repos.toml`, see `../repos.example.toml`); `start_run` rejects anything else. Three MCP tools are used to pick one:

| Tool | What it gives |
|---|---|
| `list_repos` | The whitelist: name, description, responsible owner, tags |
| `inspect_repo` | Facts from the GitHub API: README excerpt, topics, languages, dependencies from `pyproject.toml` / `requirements.txt` / `package.json` / `pom.xml` / `build.gradle` / `go.mod` / `Cargo.toml`, top-level layout, workflows and the latest default-branch CI result, last commit, open PRs, license, CONTRIBUTING, push permission |
| `find_repos` | Checks the hard criteria (language, framework, license, CI passing, has CI, active within N days, paths, push permission, CONTRIBUTING) against every whitelisted repo, reporting passed / failed / unknown for each |

What a project is *about* is not scored by rules. The model reads the description and README and judges topic fit itself. A fact that couldn't be fetched (for example, the token can't read Actions) is reported as `unknown`, not as a failure.

Example: «найди репозиторий на Python с fastapi, активным CI и лицензией MIT» becomes `find_repos(language="python", framework="fastapi", ci_passing=true, license="MIT")`.

**Confirmation is enforced in code** (`can_use_tool`), not only in the prompt. `start_run` shows «Запустить в X?» with buttons. You can also answer in words: «да» confirms, and anything else cancels and is passed to the model as feedback. The run starts without asking only when the whitelist has a single repo or you named the repo yourself. A `start_run` without a repo (in-place mode) is always refused here.

## Run

The SDK runs the Claude Code CLI bundled with it, so it uses the same credentials Claude Code does. By default that is your Claude subscription's OAuth session: either `claude login` on the machine, or a token from `claude setup-token` in `CLAUDE_CODE_OAUTH_TOKEN` (handy for a server or container). No proxy is needed: cursor-proxy, which agentflow's roles use, translates OpenAI-format requests onto that same session, and the SDK already speaks Anthropic's format.

This bot needs its own token from @BotFather: agentflow's bot uses a webhook, and a token can't have a webhook and long polling at the same time. Put both bots in one group if you want a single chat.

```bash
uv run agentflow-mcp --http                  # in agentflow: MCP on :8001
cd chat
export CHAT_TELEGRAM_BOT_TOKEN=...           # the second bot
export CHAT_ALLOWED_CHAT_IDS=123456789       # empty refuses everyone
claude setup-token                           # once: prints a long-lived OAuth token for your Claude subscription
export CLAUDE_CODE_OAUTH_TOKEN=...           # or run `claude login` on this machine, or set ANTHROPIC_API_KEY
uv run agentflow-chat
```

| Variable | Default | |
|---|---|---|
| `CHAT_MCP_URL` | `http://127.0.0.1:8001/mcp` | agentflow's MCP over streamable HTTP |
| `CHAT_MCP_TOKEN` | empty | sent as `Authorization: Bearer` if the MCP server sits behind a proxy with auth |
| `CHAT_ANTHROPIC_BASE_URL` / `CHAT_ANTHROPIC_AUTH_TOKEN` | empty | route the model through a gateway that speaks the Anthropic Messages API; empty uses `ANTHROPIC_API_KEY` or a logged-in Claude Code session |
| `CHAT_MODEL` / `CHAT_EFFORT` | `claude-opus-5-5` / `medium` | |
| `CHAT_CONFIRM_TIMEOUT_S` | `600` | an unanswered confirmation cancels the run |

`/new` drops the conversation. Sessions live in memory, one per chat.

## Development

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q
```

Tests fake the SDK session and Telegram, so they need no network.

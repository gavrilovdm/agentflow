# agentflow

Родитель: [hub/agents-ai](https://github.com/gavrilovdm/hub/blob/main/agents-ai/AGENTS.md)

Связи: предшественник — [agentic-workflow-v1](https://github.com/gavrilovdm/agentic-workflow-v1); eval-фикстура — [agentflow-sandbox](https://github.com/gavrilovdm/agentflow-sandbox); список целевых репо — `repos.example.toml`

## Что это

Мультиагентный coding-воркфлоу на LangGraph: фича-запрос (CLI, REST, MCP или GitHub issue) → спека и план с одобрением человека → по каждой задаче падающий acceptance-тест, кодер, детерминированный гейт и ревьюер → PR. Есть chat-слой, evals и интерактивный гайд (`guide/`).

## Как работать

Рабочие правила, команды и соглашения — в [`CLAUDE.md`](CLAUDE.md); обзор — в [`README.md`](README.md). Прочитай оба перед изменениями.

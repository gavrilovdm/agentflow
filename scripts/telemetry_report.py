"""The measurements table of the board, from Postgres stage_runs (see agentflow.telemetry).

uv run python scripts/telemetry_report.py                       # every repo, last 30 days
uv run python scripts/telemetry_report.py --repo gavrilovdm-dev/mentor-platform --days 7
"""

from __future__ import annotations

import argparse
import asyncio

import psycopg

from agentflow.config import get_settings

BY_AGENT = """
SELECT agent, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY seconds),
       count(*) FILTER (WHERE outcome = 'error'), sum(llm_calls), sum(input_tokens), sum(output_tokens)
FROM stage_runs WHERE started_at > now() - make_interval(days => %(days)s)
  AND (%(repo)s::text IS NULL OR repo = %(repo)s)
GROUP BY agent ORDER BY min(started_at)
"""
BY_CARD = """
SELECT repo, card, min(card_type), count(*), max(attempts),
       extract(epoch FROM max(started_at + seconds * interval '1 second') - min(started_at))::float8 / 60,
       sum(seconds) / 60, sum(input_tokens + output_tokens), (array_agg(outcome ORDER BY started_at DESC))[1]
FROM stage_runs WHERE started_at > now() - make_interval(days => %(days)s)
  AND (%(repo)s::text IS NULL OR repo = %(repo)s)
GROUP BY repo, card ORDER BY min(started_at)
"""
ERRORS = """
SELECT started_at, agent, repo, card, left(error, 160) FROM stage_runs
WHERE outcome = 'error' AND started_at > now() - make_interval(days => %(days)s)
  AND (%(repo)s::text IS NULL OR repo = %(repo)s)
ORDER BY started_at DESC LIMIT 10
"""


def table(head: list[str], rows: list[tuple]) -> str:
    def cell(v: object) -> str:
        return f"{v:.1f}" if isinstance(v, float) else ("—" if v is None else str(v))

    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return "\n".join(lines + ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    q = {"repo": a.repo, "days": a.days}
    async with await psycopg.AsyncConnection.connect(get_settings().database_url) as conn:
        agents = await (await conn.execute(BY_AGENT, q)).fetchall()
        cards = await (await conn.execute(BY_CARD, q)).fetchall()
        errors = await (await conn.execute(ERRORS, q)).fetchall()
    print(f"## Стадии ({a.repo or 'все репо'}, {a.days} дн.)\n")
    print(table(["агент", "запусков", "p50 с", "p90 с", "ошибок", "LLM-вызовов", "токенов in", "токенов out"], agents))
    print("\n## Карточки\n")
    print(table(["репо", "#", "тип", "стадий", "возвратов", "от первой до последней, мин", "работа агентов, мин",
                 "токенов", "последний исход"], cards))  # fmt: skip
    if errors:
        print("\n## Последние ошибки\n")
        print(table(["когда", "агент", "репо", "#", "ошибка"], errors))


if __name__ == "__main__":
    asyncio.run(main())

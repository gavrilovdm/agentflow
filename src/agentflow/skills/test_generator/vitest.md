---
name: vitest
description: Framework, import-path and mocking rules for acceptance tests in TypeScript repos
languages: typescript
---
- Use vitest: `import { describe, it, expect, vi } from "vitest"`. ESM imports only.
- Tests run in jsdom. For UI behaviour, render the component with `@testing-library/react`
  (`render`, `screen`) and assert on what the user sees; jest-dom matchers (`toBeInTheDocument`,
  `toHaveAttribute`) are available. The gate provides these packages — the repo need not have them.
- Mock ALL external dependencies (API calls, file I/O, DB) with `vi.mock()`.
- Test names describe the scenario: "returns 404 when user not found".
- The file is saved to `{acceptance_dir}/<name>.test.ts` (`.test.tsx` when it contains JSX), TWO
  directories below the root. Import sources with `../../src/...` — `../src/...` resolves to tests/src
  and every test fails to load.
- Cover: happy path, at least one error case, at least one edge case.

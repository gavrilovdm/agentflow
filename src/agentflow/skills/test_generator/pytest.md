---
name: pytest
description: Framework, import-path and mocking rules for acceptance tests in Python repos
languages: python
---
- Use pytest. Plain `assert` statements, fixtures over setup methods.
- Mock ALL external I/O (HTTP, DB, filesystem outside tmp_path) with unittest.mock / monkeypatch.
- Each test is deterministic and named for the scenario: test_returns_404_when_user_missing.
- The file is saved to `{acceptance_dir}/`. Import the code under test by its package path
  as installed from the repo root (e.g. `from mypkg.users import get_user`), never relative imports.
- Cover: happy path, at least one error case, at least one edge case.

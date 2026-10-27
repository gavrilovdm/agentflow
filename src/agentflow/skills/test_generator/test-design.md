---
name: Test design
description: What makes a test worth having, whatever the framework
---
- Test behaviour through the public interface, never internals: a test that reaches into private
  state breaks on every refactor and proves nothing about what callers see.
- One behaviour per test, in three visible parts: arrange the inputs, act once, assert the outcome.
- Assert exact values and errors (`== 3`, raises `NotFound`), not truthiness or "did not crash".
- Pick inputs at the boundaries: empty, one, many, zero, negative, the maximum, missing and malformed.
  Most bugs live where a range starts or ends.
- Mock only what crosses the process boundary (network, database, clock, randomness, filesystem).
  Mocking the unit under test, or its own helpers, makes the test check the mock.
- No sleeps, no real time, no dependence on test order: a flaky test is worse than none, because
  every failure gets ignored.

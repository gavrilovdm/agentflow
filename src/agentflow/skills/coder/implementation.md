---
name: Implementation
description: Writing the smallest correct change in the style of the code around it
---
- Read before you write: the file you change, its callers, and one sibling that already does something
  similar. Match its naming, error handling and structure even where you would choose differently.
- Make the smallest change that meets the Definition of Done. Do not refactor, rename or reformat code
  the task does not need; unrelated edits hide the real change and can break callers.
- Fail loudly at the boundary: validate input where it enters, raise a specific error, never swallow
  an exception or return a silent default that hides a bug.
- Keep secrets, paths and environment-specific values out of code; use the configuration the project
  already has.
- When an attempt was rejected, fix the cause the feedback names, then check the rest of your change for
  the same mistake. Rewriting from scratch usually reproduces it.
- Leave no dead code, commented-out code, debug output or TODOs behind.

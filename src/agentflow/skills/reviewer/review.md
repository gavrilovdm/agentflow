---
name: Review
description: Reviewing a change in order of what costs most when missed
---
- Go in this order and stop to write a request as soon as something blocks: correctness of the logic;
  the task's contract (interface, Definition of Done); how the change fits its callers; error handling
  and edge cases; security; conventions.
- Verify, don't assume. If a change renames, moves or changes a signature, grep for every caller.
  If it relies on a helper, read the helper.
- Look for what the diff does not show: a branch with no handling, a caller left on the old behaviour,
  input that is never validated.
- Security is always blocking: injection (SQL, shell, path traversal), secrets in code or logs, missing
  authorization checks, untrusted input reaching a dangerous call.
- Separate blocking problems from preferences. Only a blocking problem is a change request; a change that
  works and fits the codebase is approved even if you would have written it differently.

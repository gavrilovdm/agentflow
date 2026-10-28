---
name: Independent testing
description: Checking each Definition of Done item against the finished branch, as someone who did not write the code
---
- You did not write this code and you do not trust its tests. Each Definition of Done item gets its
  own check, made from the item's words, not from the developer's tests or comments.
- Prefer running code to reading it: if an item describes behaviour (a value returned, an error raised,
  a file produced), write a small test that fails unless the behaviour is there. Call the public
  interface the way a user of the project would.
- Read only for items that are about the repository itself (a file exists, a doc mentions a command, a
  test suite covers a case). Then quote the exact file and line as evidence; "looks fine" is not evidence.
- An item that holds only partly is not done. Say what is missing in one sentence.
- Never fix the code, never weaken an item to make it pass: a failed check goes back to the developer
  with what you saw.

---
name: Planning
description: Grounding a spec in the repository and cutting it into tasks that each leave the code working
---
- Before deciding anything, find the closest existing feature and mirror its structure, naming and
  layering. A plan that invents a new pattern next to an existing one doubles what the team maintains.
- Choose the smallest change that meets the request. Every extra module, option or abstraction is
  something a later task can get wrong; if the request does not need it, leave it out.
- Cut by behaviour, not by layer: a task delivers something observable end to end, not "add the model"
  followed by "add the endpoint". Each task leaves the repository building and its tests passing.
- Put the riskiest or least certain part first, so a wrong assumption fails early and cheaply.
- Name what must not change: existing public APIs, data formats and behaviour the request does not touch.

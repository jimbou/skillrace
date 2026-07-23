# DeepSeek V3.2 provisional development pricing

**Recorded:** 2026-07-13  
**Scope:** development accounting only; not a frozen headline rate card

The project owner transcribed the following values from Yunwu's model-pricing UI:

| Token class | Yunwu custom credits per million tokens |
| --- | ---: |
| Input | ⚡2.00 |
| Completion | ⚡3.00 |

No separate V3.2 prompt-cache-read price was supplied. Development accounting therefore
charges cache-read tokens conservatively at the ordinary input rate, ⚡2.00/M. The code
labels this source `yunwu-user-reported-rate-card/2026-07-13-v3.2-v1` and writes that
identifier into every V3.2 development receipt.

This record permits transparent development cost accounting; it does **not** authorize a
headline track. If V3.2 is selected in the final hardcoded inventory, capture a dated,
reviewable provider rate-card archive (including any cache policy), then build its images,
preflight it, and freeze its schedules before the first headline observation.

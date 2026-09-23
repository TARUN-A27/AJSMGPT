# V1 freeze criteria — decided 2026-09-23

`progress.md` names Step 7 "Freeze V1" with no definition of done. This was a proposal; Tarun
confirmed all three open points on 2026-09-23 (below). What's left is mechanical (bigger question
bank, then measure), not a decision.

## Why one number isn't the right question

"How many of the 47 questions pass" mixes two different things:
- **Depth** — for a family V1 already claims to support (purchase, mrs, consumption, supplier
  lookup, material lookup), does it answer *unseen* phrasings correctly? This is what a pass-rate
  number should measure, and only on a held-out set the prompts were never tuned against —
  today's 47 have been looked at too many times to trust for this.
- **Coverage** — which families are in V1 at all. 24 of today's 47 questions fail by design
  (stock, GRN, attendance — no catalog family exists). That is not a depth problem; no prompt or
  model change fixes it. It is bought by adding a family, one at a time.

A single freeze number conflates "the 5 families we have are solid" with "we only built 5
families," and would let either one hide behind the other.

## Proposed exit criteria (two-part)

**1. Coverage: which families are in v1.0 — ✅ done 2026-09-23**
purchase, mrs, consumption, supplier lookup, material lookup, **stock and GRN** (fix.md #13).
Live-verified against the real server: `check_schema_access.py` confirms `ajsmgpt_ro` can read
`ITEMSTOCK`/`GRN`; the offline and live evaluators both exercise the new families.
**Attendance decided: deferred, not in v1.0** (confirmed 2026-09-23) — unlike stock/GRN, it has
never had a read-only profiling pass (verified columns, verified joins, known row-shape gotchas);
adding it now would be unplanned scope into a domain nobody has studied yet, not a quick addition.
It needs its own study pass first, same shape as the original Oracle schema study, before it can
be catalogued the way stock/GRN were. Everything else (the PO-pending SO/IA/JMD ladder, the
MRS-pending anti-join, cross-family joins) stays deferred to v1.1+ per `progress.md`'s Post-V1
roadmap, unchanged.

**2. Depth: pass rate on a held-out set — bar confirmed: ≥75%, measured per family, not blended**
Purchase/MRS/consumption already have real passes; stock/GRN were only added 2026-09-23 and are
unproven on live data beyond the gap-finding that same session did. Blending all families into one
aggregate number would let a strong established family hide a weak new one, so the bar applies
**per family** — flag any single family that falls far below 75% rather than averaging it away.
Still needs a bigger question bank first (`data/question_bank_v1.json` is short ~40 questions
across consumption/GRN/material-lookup). Once enough exist, hold back roughly half of each family
as a test set never used to tune prompts or catalog, and measure against *that* half.

**Non-negotiable regardless of the number: zero wrong answers — confirmed as-is, not loosened.**
A refusal is acceptable — it's the system working as designed. A confidently wrong number is not,
and is exactly what the validator stack built this week (fail-closed grounding, GROUP BY grain
check, half-open date binds, the fix.md #10/#12 fixes) exists to prevent. Any single wrong answer
in the held-out set blocks freeze regardless of the pass-rate number.

## What's left before this can be measured for real
- ~~The DBA-created SELECT-only account~~ — ✅ done 2026-09-23, verified SELECT-only + CREATE SESSION only.
- ~~The branch pushed to `origin`~~ — ✅ done.
- ~~Deploy + verify against live Oracle~~ — ✅ done: V1 running at `/home/ajsmgpt/AJSMGPT_v1` beside the
  legacy service; the live evaluator ran 47/47 questions against real Oracle with 0 crashes (fix.md #13).
- The expanded question bank (consumption/GRN/material-lookup, ~40 more real questions) — still open.
- 3 golden (question, verified-correct-answer) pairs per family, for the live spot-check
  `progress.md`'s verification section calls for — still open; run 3/4 of the live eval produced
  the first real `PASS_PIPELINE` (fix.md #13), so at least one golden pair is now possible; still
  need 2 more per family.

## Decided 2026-09-23 (Tarun)
- Depth bar: **75%, per family** (not blended).
- Zero wrong answers: **confirmed as-is**, not loosened.
- Attendance: **deferred to v1.1**, not in v1.0 coverage.

Nothing left to decide here — the remaining work is the question bank (mechanical, on Tarun) and
then the measurement itself (on Claude, once the bank exists).

# V1 freeze criteria — proposal, awaiting Tarun's sign-off

`progress.md` names Step 7 "Freeze V1" with no definition of done. This is a proposal, not a
decision — nothing here changes until Tarun confirms or picks a different bar. Written now so
Step 6 has a concrete target to measure against instead of an open-ended "some day."

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
Proposed and now built: purchase, mrs, consumption, supplier lookup, material lookup, **stock and
GRN** (fix.md #13). Live-verified same day against the real server: `check_schema_access.py`
confirms `ajsmgpt_ro` can read `ITEMSTOCK`/`GRN`; the offline and live evaluators both exercise the
new families. Everything else (attendance, the PO-pending SO/IA/JMD ladder, the MRS-pending
anti-join this session also found and recorded, cross-family joins) stays deferred to v1.1+,
unchanged from `progress.md`'s existing Post-V1 roadmap.

**2. Depth: pass rate on a held-out set, for the families above only**
Needs a bigger question bank first (`data/question_bank_v1.json` is short ~40 questions across
consumption/GRN/material-lookup — see the question-bank gap analysis from this session). Once
enough exist, hold back roughly half of each family as a test set never used to tune prompts or
catalog, and measure against *that* half. Proposed bar: **≥ 75% of the held-out set**, with the
non-negotiable second condition below.

**Non-negotiable regardless of the number: zero wrong answers.** A refusal is acceptable — it's
the system working as designed. A confidently wrong number is not, and is exactly what the
validator stack built this week (fail-closed grounding, GROUP BY grain check, half-open date
binds, the fix.md #10/#12 fixes) exists to prevent. Any single wrong answer in the held-out set
blocks freeze regardless of the pass-rate number.

## What's left before this can be measured for real
- ~~The DBA-created SELECT-only account~~ — ✅ done 2026-09-23, verified SELECT-only + CREATE SESSION only.
- ~~The branch pushed to `origin`~~ — ✅ done.
- ~~Deploy + verify against live Oracle~~ — ✅ done: V1 running at `/home/ajsmgpt/AJSMGPT_v1` beside the
  legacy service; the live evaluator ran 47/47 questions against real Oracle with 0 crashes (fix.md #13).
- The expanded question bank (consumption/GRN/material-lookup, ~40 more real questions) — still open.
- 3 golden (question, verified-correct-answer) pairs per family, for the live spot-check
  `progress.md`'s verification section calls for — still open; needs a real `PASS_PIPELINE` to check
  against, and the first live run had none (see fix.md #13's live-eval numbers).

## Open decision for Tarun
- Confirm or replace: the 75% depth bar, the stock+GRN coverage addition, or the "zero wrong
  answers" condition (this one is not really negotiable given what §3 of `CLAUDE.md` asks for,
  but say so if a different reading is intended).
- Whether attendance (`HRDNEW.CURRENTATTENDANCE`) belongs in v1.0 coverage or stays deferred —
  it appears in real questions but has never been profiled the way stock/GRN have.

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
be catalogued the way stock/GRN were. Everything else in that Post-V1 list was, as of this morning,
deferred to v1.1+ per `progress.md`'s Post-V1 roadmap. **Update, same day:** two of those items — the
PO-pending SO/IA/JMD ladder and the MRS-pending anti-join — were pulled forward and implemented on
2026-09-23 at Tarun's explicit direction (fix.md #4; see `progress.md`'s coverage table and log). This
is a reprioritization, not a reversal of the reasoning above — attendance still needs its own study
pass for the reasons already given, and that reasoning is unaffected. Cross-family joins stays
deferred to v1.1+, unchanged.

**2. Depth: pass rate on a held-out set — bar confirmed: ≥75%, measured per family, not blended**
Purchase/MRS/consumption already have real passes; stock/GRN were only added 2026-09-23 and are
unproven on live data beyond the gap-finding that same session did. Blending all families into one
aggregate number would let a strong established family hide a weak new one, so the bar applies
**per family** — flag any single family that falls far below 75% rather than averaging it away.
~~Still needs a bigger question bank first~~ — ✅ done (line below). ~~Hold back roughly half of
each family as a test set never used to tune prompts or catalog~~ — ✅ mechanism built 2026-09-24:
`scripts/evaluate_v1_real_questions.py`'s `_select_questions(split=...)` now supports `"train"` /
`"test"` / `"all"`. `"test"` is a deterministic hash of each question's own text (stable across runs,
no persisted field to drift out of sync), **with every Claude-generated golden-pair question forced
into `"train"`** — those were written with full visibility into what the catalog supports, so they
can never count as blind held-out data. Two of the smaller pools are thin on the test side purely
from small-N: material_lookup (2) and consumption (2) — real per-family pass/fail is noisy for
those two until the bank grows more, not a code defect.

**Measured 2026-09-24, on the server against real Oracle + qwen3:14b: every family fails the 75%
bar.** 61 in-scope questions (out_of_scope_hr/admin correctly excluded, refused by design): 3
`PASS_PIPELINE` total. purchase 1/20 (5%), mrs 1/10 (10%), consumption 1/2, material_lookup 0/2,
stock 0/10, supplier_lookup 0/10, grn 0/7. This is the real signal the tuned 47-question set
couldn't show — genuinely unseen phrasing lands on gaps the tuned set never reached. Root cause by
family (diagnosed, not yet fixed):
- **stock (0/10):** one pattern — the model picks `operation=detail` for "what is the stock of X",
  but stock's capability only allows `aggregate` (ITEMSTOCK is 1–31 rows/item, aggregate-only by
  design). Same shape as fix.md #3's MRS lookup/detail prompt gap; looks like a single fast fix.
- **grn (0/7):** several distinct grounding gaps — ambiguous concept→column mapping ("qty
  received", "GRN for supplier X" each match >1 catalog column), missing concepts ("received
  material names", "for order X"), and domain misrouting on entity-first phrasing ("grn for yarn").
- **supplier_lookup (0/10):** every failure is "who supplies item X" — a reverse item→supplier
  lookup, structurally different from the golden pairs' "is X a registered supplier" pattern. May
  simply not be implemented; needs scoping before it's a "fix."
- **mrs (1/10):** mix of genuine QueryPlan-contract failures and grounding gaps for concepts that
  may not be cataloged at all ("material approval pending", "material hold", "indent number X").
- **purchase (1/20):** one `SQL_EXECUTION_FAILURE` — a query that passed every validator layer
  still broke on real Oracle, but the record has no captured detail (`sql_preview`/`cause` both
  `None`; `OracleExecutionError`'s message alone doesn't carry the underlying Oracle error and no
  `__cause__` was chained) — needs a targeted re-run with better instrumentation before it's
  diagnosable. The rest are `ENTITY_RESOLUTION_REJECTION` (see next point).

**Open methodology question, not decided unilaterally:** a large share of the
`ENTITY_RESOLUTION_REJECTION` failures across families are the fix.md #2 fuzzy fallback correctly
surfacing real multi-candidate ambiguity for shorthand entities ("keyboard", "monitor", "yarn") —
arguably a safe, by-design refusal per this doc's own "a refusal is acceptable" line, not a defect.
A few others are the model mis-treating a non-entity word ("pending") as a resolvable entity — a
genuine extraction bug, not safety working correctly. Whether ambiguous-with-real-candidates counts
as depth-bar "pass" or "fail" changes several of the numbers above materially; left open for Tarun
rather than picking an interpretation that produces a cleaner-looking percentage.

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
- ~~The expanded question bank~~ — ✅ largely done 2026-09-24: 16 already-real, already-logged
  consumption/GRN/material-lookup questions found sitting unused in `data/question_bank_v1.json`
  (never wired into the evaluator's selection), plus 26 new questions Claude generated, grounded
  against real Oracle entities, and computed answers for directly (bypassing the V1/Qwen pipeline
  entirely, so it's an independent check) — **all 26 confirmed correct by the client**. Both added
  to `data/question_bank_v1.json` (now 223 questions). ~~Still open: wire the evaluator to this
  bank~~ — ✅ done 2026-09-24: `scripts/evaluate_v1_real_questions.py`'s `_select_questions()` now
  reads `data/question_bank_v1.json` directly (was the smaller `AutomateQuery/reports/question_bank.json`)
  and buckets by its `proposed_family` field instead of keyword-matching a generic `category`. Every
  one of the 7 v1.0 families now has a real per-family cap (material_lookup/consumption/grn/stock/
  supplier_lookup/mrs 10, purchase 20, out_of_scope 8) — material_lookup had no bucket at all before
  today. Selection went 47→81 questions. `scripts/evaluate_v1_real_questions_live.py` (the live-Oracle
  variant run on the server) imports `_select_questions()` from this module, so it picked up the fix
  with no separate change needed. The held-out train/test split (line below) was built the same day.
- ~~3 golden (question, verified-correct-answer) pairs per family~~ — ✅ done 2026-09-24: 26 pairs
  across all 7 families (material_lookup 3, consumption 4, grn 5, stock 3, purchase 7, supplier_lookup
  2, mrs 2), client-verified. The verified **answers** (real business figures) are deliberately kept
  out of git per §3 — they exist only in the hand-off document Tarun's client verified, not in the
  tracked codebase. Only the **questions** (no answers) were added to `data/question_bank_v1.json`.

## Decided 2026-09-23 (Tarun)
- Depth bar: **75%, per family** (not blended).
- Zero wrong answers: **confirmed as-is**, not loosened.
- Attendance: **deferred to v1.1**, not in v1.0 coverage.

The question bank, split mechanism, and the measurement run are all done 2026-09-24 — see "Measured
2026-09-24" above. **Freeze is not ready by the letter of this bar**: every family is far below
75% on genuinely unseen phrasing. Two things now need Tarun's input before more effort goes in:
(1) the ambiguous-entity-refusal methodology question above, which changes several numbers
materially; (2) which of the five root-caused failure clusters to actually fix, and in what order
— stock looks like one fast, high-confidence fix; supplier_lookup's gap may be a scope decision
(is "who supplies item X" even meant to be in v1.0?) rather than a bug.

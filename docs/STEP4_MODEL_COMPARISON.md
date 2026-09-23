# Step 4 — Qwen3:8b vs Qwen3:14b comparison protocol

Measurement only, per CLAUDE.md §5 Step 4. No Oracle, no opportunistic fixes — record
what each model does, decide the runtime model afterward as its own step.

## What is held fixed
- **Questions:** the same 47 in `data/evaluation_questions.json`, same order.
- **Code:** `scripts/evaluate_v1_real_questions.py`, unmodified — the real
  `app/nlp_execution.execute_nlp_query` chain, with only the runner (stub, never
  connects) and entity resolution (offline fixture lookup) substituted, exactly as
  documented in that script's own header. Zero Oracle calls either run.
- Neither run touches any file on the server; `.env` is untouched on both ends.

## What is varied — model AND host (correction from the original draft)
The company server (`ssh -p 5555 ajsmgpt@103.171.13.142`, RTX 5070, Ollama 0.30.10)
holds `qwen3:14b` and `nomic-embed-text` only — **`qwen3:8b` was never pulled there**
(confirmed: the first attempt 404'd "model not found" on every question in <1s). Pulling
an extra 5 GB model onto a machine that already has the one this comparison actually
needs was out of scope, so the comparison is instead:
- **qwen3:8b:** the run already committed at `64e901e` (laptop, local Ollama) — same
  code, same questions, not re-run.
- **qwen3:14b:** run fresh via an SSH tunnel (`-L 11435:localhost:11434`) to the
  server's real Ollama, opened for this comparison only.

**Consequence:** the classification comparison (§ below) is unaffected — it depends
only on what each model outputs, not on hardware. The **latency** comparison is not
apples-to-apples (different CPU/GPU, different network hop) and is reported as a
rough figure, not a verdict.

```bash
OLLAMA_URL=http://localhost:11435 OLLAMA_CHAT_MODEL=qwen3:14b ./venv/bin/python scripts/evaluate_v1_real_questions.py
```
Result copied to the scratchpad before it can be overwritten by anything else.

## What decides
1. **Classification distribution**, in-scope buckets only (the 23 not marked
   `expected_unsupported`): `PASS_PIPELINE` up is the goal; `QUERY_PLAN_FAILURE` and
   `SQL_VALIDATION_FAILURE` down is the goal. `ENTITY_RESOLUTION_REJECTION` and
   `CAPABILITY_FAILURE` are not model-quality signals here — they're fixture/catalog
   gaps already tracked in fix.md #2/#3, so a model can't move them.
2. **Per-question diff**, not just the totals — a count staying flat can hide one
   regression cancelling one improvement. Every question whose bucket changes is
   listed with old → new.
3. **Latency**, because it's a real product cost: total wall time and per-question
   average, both models on the same GPU.
4. **New failure modes** — does 14b ever produce SQL the validator now has to catch
   that 8b's SQL never triggered (a bigger model writing more confident, more
   elaborate — not necessarily more correct — SQL)?

## Decision rule
Adopt 14b as the **dev/eval** model only if it improves (1) without a latency
increase that would make the interactive question-answer loop unacceptable, and
without new failure modes in (4). This does not by itself change the production
runtime model — that is Step 5 (acceptance re-run) and Step 6 (live Oracle
confirmation), taken as their own steps once this measurement is in.

## Result
See fix.md #11 for the full numbers. Summary: `PASS_PIPELINE` 2 → 5, `QUERY_PLAN_FAILURE` 4 → 1,
`SQL_VALIDATION_FAILURE` 1 → 0, across the 23 in-scope questions; 8 of 47 total questions
changed bucket, all but one improving. **Decision: qwen3:14b is adopted as the dev/eval model**
(Step 5 and further prompt work). Does not change the production runtime default by itself —
that is Step 6's job, on the real Oracle server, as its own confirmation.

One of the per-question moves surfaced a pre-existing gate bug unrelated to model choice — see
fix.md #12 (capability/grounding's "lookup" shortcut discards `plan.domain`). Recorded, not
fixed today; it is not specific to 14b and would affect 8b identically if the model ever tagged
a question the same way.

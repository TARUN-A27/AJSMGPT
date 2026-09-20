# AJSMGPT

## 1. Project
AJSMGPT is an Oracle ERP natural-language query assistant.

## 2. Current V1 Architecture
```text
raw question
→ safe typo correction
→ spaCy NLP signals
→ Qwen structured QueryPlan
→ semantic validation
→ deterministic GroundedSchemaPlan
→ Qwen grounded Oracle SQL
→ strict static SQL validation
→ safe read-only Oracle execution
→ deterministic business report
```

## 3. V1 Entry Points
Current V1 API is `/v1/nlp/*`:
- `/v1/nlp/analyze`
- `/v1/nlp/understand`
- `/v1/nlp/sql-preview`
- `/v1/nlp/execute`

Do not invent additional endpoints.

## 4. Legacy Pipeline
The following belong to the legacy/old pipeline and are **NOT** the V1 production architecture:
```text
/ask
app/query_engine.py
app/purchase_analytics_router.py
app/sql_generator_v2.py
app/schema_search.py
```

Important:
- `/ask` currently remains registered.
- Do not remove or modify it unless a task explicitly requests migration/deprecation work.
- Legacy tests may remain as regression/reference material.
- Do not use legacy modules as the basis for new V1 implementation.

## 5. Unwired Files
`app/query_planner.py` is currently unwired and must NOT be assumed to be part of the V1 execution chain unless a future task explicitly integrates it.

## 6. Architecture Rules
- Prefer deterministic validation over LLM assumptions.
- Never infer unverified Oracle business semantics.
- Fail closed when schema/business meaning is not verified.
- V1 SQL must be grounded in the verified business schema catalog.
- Never bypass SQL safety validation.
- Oracle access is read-only.
- Do not add live metadata lookups where offline verified metadata is required.
- Do not use the old SQL builder as the production architecture.

## 7. Coding Rules
- Work only within the explicitly requested task.
- Do not perform unrelated refactors.
- Do not expand scope automatically.
- Preserve unrelated dirty worktree changes.
- Never use `git add .`.
- Do not modify `.env`.
- Never expose credentials, DSNs, or ERP result rows.
- Do not reset, restore, clean, or delete unrelated work.
- Stop when the requested acceptance criteria are satisfied.

## 8. Testing Rules
- Prefer targeted tests for the changed component.
- Do not run Oracle/network tests on the laptop.
- Do not run Ollama unless explicitly requested.
- Do not rerun the entire suite when targeted tests are sufficient.
- Report exactly which tests were run and their result.

## 9. Task Discipline
Every coding task must have a bounded scope and explicit acceptance criteria. Do not autonomously continue into the next task.

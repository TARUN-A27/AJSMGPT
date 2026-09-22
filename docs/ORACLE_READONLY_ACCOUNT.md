# Read-only Oracle account for AJSMGPT V1

Why: the account V1 currently inherits (`INVENTORY`) owns the INVENTORY schema and holds INSERT/UPDATE/DELETE
grants on parts of SCM, HRDNEW, ADMIN and INSUR, plus `CREATE ANY VIEW` / `CREATE ANY SYNONYM` / `UNLIMITED
TABLESPACE` (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §0). AJSMGPT's read-only guarantee (CLAUDE.md §3) is therefore
enforced only by application code today. A SELECT-only account makes it a database guarantee.

This file is a request for the DBA. AJSMGPT never runs DDL or GRANT statements itself.

## Statements for the DBA (Oracle 11.2)

```sql
-- 1. account: no quota, no object-creation privileges, session only
CREATE USER ajsmgpt_ro IDENTIFIED BY "<set by DBA>"
  DEFAULT TABLESPACE users QUOTA 0 ON users
  PROFILE default;
GRANT CREATE SESSION TO ajsmgpt_ro;

-- 2. the tables V1 reads today (catalog + entity resolution)
GRANT SELECT ON INVENTORY.PURCHASEORDER TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.INVITEMS      TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.MRS_TEMP      TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.ISSUE         TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.DEPT          TO ajsmgpt_ro;
GRANT SELECT ON SCM.PARTYMASTER         TO ajsmgpt_ro;

-- 3. tables the next catalog families will read (V1.1: stock, GRN, approved MRS, geography for supplier display)
GRANT SELECT ON INVENTORY.MRS           TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.GRN           TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.INVOICEGRN    TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.ITEMSTOCK     TO ajsmgpt_ro;
GRANT SELECT ON INVENTORY.UNIT          TO ajsmgpt_ro;
GRANT SELECT ON SCM.PLACE               TO ajsmgpt_ro;
GRANT SELECT ON SCM.STATE               TO ajsmgpt_ro;
GRANT SELECT ON SCM.COUNTRY             TO ajsmgpt_ro;
```

Nothing else: no `SELECT ANY TABLE`, no role with DML, no `EXECUTE` on packages, no access to `SCM.BANKDETAILS`,
`SCM.PARTYMASTER` bank/PAN/GSTIN columns beyond what `SELECT ON PARTYMASTER` implies (V1 never selects them — the
catalog only exposes PARTYCODE / PARTYNAME / GOODSTYPECODE), and no HRDNEW/ADMIN/INSUR access until a family needs it.

Data-dictionary views (`ALL_TABLES`, `ALL_TAB_COLUMNS`, `ALL_CONSTRAINTS`, …) need no extra grant; they show the
account exactly the objects it can select, which is what `scripts/check_schema_access.py` and the metadata
extractors rely on.

## What AJSMGPT needs after the account exists

1. `.env` on the server (`ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN` unchanged) — set by the operator, never
   written by AJSMGPT (CLAUDE.md §3).
2. Verify from the server, read-only:
   ```bash
   ./venv/bin/python scripts/check_schema_access.py
   ```
   Expected: SELECT works on every table in §2, `ALL_TAB_PRIVS` shows only SELECT rows for `AJSMGPT_RO`, and
   `SELECT privilege FROM user_sys_privs` returns nothing beyond `CREATE SESSION`.
3. Only then run Step 6 (`scripts/evaluate_v1_real_questions.py` against live Oracle) with that account.

## Deployment runbook (Step 6 entry) — pending Tarun's go-ahead

The live `ajsmgpt-api.service` runs the legacy `/ask` from `/home/ajsmgpt/AJSMGPT_git` on :8000. V1 runs **beside**
it, never replaces it before the V1 freeze:

```bash
# on the server, as ajsmgpt
git clone -b feature/v1-query-execution git@github.com:TARUN-A27/AJSMGPT.git /home/ajsmgpt/AJSMGPT_v1
cd /home/ajsmgpt/AJSMGPT_v1
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt && ./venv/bin/python -m spacy download en_core_web_sm
cp /home/ajsmgpt/AJSMGPT/.env .env          # then set ORACLE_USER/ORACLE_PASSWORD to the read-only account
./venv/bin/python -m unittest scripts.test_schema_grounding scripts.test_nlp_execution   # offline sanity
./venv/bin/python scripts/check_schema_access.py                                          # read-only access check
```
User systemd unit `~/.config/systemd/user/ajsmgpt-v1.service`: same shape as `ajsmgpt-api.service` with
`WorkingDirectory=/home/ajsmgpt/AJSMGPT_v1`, `EnvironmentFile=/home/ajsmgpt/AJSMGPT_v1/.env`,
`ExecStart=/home/ajsmgpt/AJSMGPT_v1/venv/bin/python -m uvicorn app.api:app --host 0.0.0.0 --port 8001`.
Prerequisite: the branch must first be pushed to `origin` (needs Tarun's approval).

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
   Expected: SELECT works on every table in §2, `USER_TAB_PRIVS` for `AJSMGPT_RO` itself shows only SELECT rows,
   and `USER_SYS_PRIVS` returns nothing beyond `CREATE SESSION`.
   **`ALL_TAB_PRIVS WHERE GRANTEE = 'PUBLIC'` is a separate, database-wide check** (added to the script
   2026-09-23) — `USER_TAB_PRIVS` only shows grants made to the named account, and does not include anything the
   account inherits via `GRANT ... TO PUBLIC`. Verified against the live account 2026-09-23, at the real scale
   (an unscoped query, not the 5-schema sample): this instance has **~28,700 PUBLIC object grants database-wide,
   ~26,500 of them beyond `SELECT`** — evidently old cross-database migration tooling (the
   `MICROSOFTDTPROPERTIES`/`MICROSOFTSEQDTPROPERTIES` pattern repeats across schemas AJSMGPT has never
   referenced, e.g. `ACCSHARES`, `ACCTEX`). Within AJSMGPT's own 5 schemas this includes `UPDATE`/`DELETE` on
   `HRDNEW.CURRENTATTENDANCE` (2.3M rows), `OVERTIME`, `SHIFTALLOCATION`, `ADMIN.ONETOUCHEMPLOYEE`, and `EXECUTE`
   on `HRDNEW.GETNAME`. All of this applies to *every* account on the instance, `ajsmgpt_ro` included, and
   predates this account entirely — not something its creation introduced and not something AJSMGPT can revoke
   (it never runs GRANT/REVOKE). The script now checks, rather than asserts, whether any of it lands on the 14
   tables AJSMGPT actually reads (§2 above): as of this verification, **none do** — every PUBLIC grant beyond
   SELECT sits on a table outside AJSMGPT's own catalog. This is a real database-hygiene item for whoever owns
   this instance, entirely independent of AJSMGPT, and far too broad for AJSMGPT (or this doc) to remediate.
3. Only then run Step 6 (`scripts/evaluate_v1_real_questions.py` against live Oracle) with that account.

## Deployment runbook (Step 6 entry) — ✅ done 2026-09-23

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
Done: branch pushed (`8d9f6f8` at push time), cloned to `/home/ajsmgpt/AJSMGPT_v1`, venv + deps + spaCy model
installed, offline core suite 284/284, `ajsmgpt_ro` created (Tarun, via SQL Developer with `system`), `.env`
copied and edited (Tarun), `check_schema_access.py` confirms `SELECT`-only + `CREATE SESSION`-only for the account
itself (the PUBLIC-grant finding above is separate and pre-existing).
**Not yet done:** the systemd unit below, and Step 6's actual live-question eval.

User systemd unit `~/.config/systemd/user/ajsmgpt-v1.service` (not yet created): same shape as `ajsmgpt-api.service`
with `WorkingDirectory=/home/ajsmgpt/AJSMGPT_v1`, `EnvironmentFile=/home/ajsmgpt/AJSMGPT_v1/.env`,
`ExecStart=/home/ajsmgpt/AJSMGPT_v1/venv/bin/python -m uvicorn app.api:app --host 0.0.0.0 --port 8001`.

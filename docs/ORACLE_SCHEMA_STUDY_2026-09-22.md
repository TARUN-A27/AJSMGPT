# Oracle ERP Schema Study — 2026-09-22

Read-only study of the company Oracle database from the AJSMGPT server, plus how AJSMGPT currently reaches it.
Method: `ALL_*` dictionary views, `COUNT(*)`, and aggregate profiles (distinct counts, min/max of date-like
columns, value distributions of small numeric code columns). No row data was read, stored, or reproduced here.
No DML/DDL/PL-SQL. Raw dictionary/profile dumps are in the session scratchpad, not in the repo.

## 0. Environment and access

| Item | Value |
|---|---|
| Database | `ARUN`, Oracle Database **11g EE 11.2.0.1.0** (2009-era; no `FETCH FIRST` — see §7) |
| Connect as | `INVENTORY` (owns the INVENTORY schema; `ISDBA = FALSE`) |
| Client | python-oracledb 4.0.1, **Thick** mode, instantclient_21 (`ORACLE_CLIENT_LIB_DIR`) |
| Session NLS | `NLS_DATE_FORMAT = DD-MON-RR`, `NLS_LANGUAGE = AMERICAN` |
| Roles / sys privs | `CONNECT`, `RESOURCE`, `JAVAUSERPRIV`; `CREATE ANY VIEW`, `CREATE ANY SYNONYM`, `UNLIMITED TABLESPACE` |
| Object privs on other schemas | SELECT on all 5 studied schemas **and** INSERT/UPDATE/DELETE on many SCM (19/12/7 tables), HRDNEW (5/12/8), ADMIN (3/5/3), INSUR (1/1/1) tables; REFERENCES/ALTER/INDEX on a few |
| Not selectable (ORA-01031) | `ADMIN.GEOPHOTO`, `HRDNEW.APPRENTICE1`, `SCM.COTTONAGN`, `SCM.PURCHASEAGN`, `SCM.TBL_ALERT_RECIPIENT_COLLECTION` |

**Security note.** "Read-only" is enforced only by AJSMGPT code (`sql_safety.validate_select_only`), not by the
database: the `INVENTORY` account can write to its own schema and to parts of the other four. A dedicated
SELECT-only Oracle account for AJSMGPT would make CLAUDE.md §3 a database guarantee instead of a code promise.

### AJSMGPT on the server (two copies, neither is V1)

| Path | State | Oracle access |
|---|---|---|
| `/home/ajsmgpt/AJSMGPT` | not a git repo; 39 `.bak_*` files from July 2026; legacy modules only (`query_engine`, `purchase_analytics_router`, `query_planner`, `hybrid_value_resolver`, `mrs_template_router`, `rasa_nlu_client`) | old `oracle_client.py`: `run_safe_select(sql)` — **no bind parameters**, no datatype validation with binds, `oracledb.connect(user, dsn)` with no password argument; `.env` sourced by the service unit |
| `/home/ajsmgpt/AJSMGPT_git` | git, branch `feature/master-purchase-analytics` (last: "Build feedback repair case pipeline"), 57 dirty files; **the live `ajsmgpt-api.service` (uvicorn :8000) runs from here** with `/home/ajsmgpt/AJSMGPT/venv` and `/home/ajsmgpt/AJSMGPT/.env` | same legacy `oracle_client.py` (md5 `cf43f21f…`); routes `/ask`, `/plan`, `/resolve`, `/plan-nlp`, `/nlp-router-candidate`, `/health/db` |

Neither copy contains `nlp_execution.py`, `nlp_router.py`, `entity_resolution.py`, `schema_grounding.py`,
`grounded_sql_*`, or `query_plan*` — the V1 pipeline (`feature/v1-query-execution`) has never been deployed.
Also on the box: Ollama (:11434), Qdrant (:6333), an unrelated Estimation_Mixing_Sheet gunicorn app.

`.env` variable names (values never read): `APP_NAME APP_ENV DEBUG OLLAMA_URL OLLAMA_CHAT_MODEL OLLAMA_EMBED_MODEL
QDRANT_URL QDRANT_COLLECTION ORACLE_USER ORACLE_PASSWORD ORACLE_DSN ORACLE_CLIENT_LIB_DIR ORACLE_SCHEMAS
SQL_ALLOWED_STATEMENTS ALLOW_ONLY_SELECT BLOCK_DDL BLOCK_DML SQL_MAX_ROWS SQL_QUERY_TIMEOUT_SECONDS QDRANT_TOP_K
EMBEDDING_DIMENSION ENABLE_SQL_GENERATION ENABLE_RESULT_EXPLANATION ENABLE_MULTI_SCHEMA_SEARCH LOG_LEVEL`.

## 1. Schemas

| Schema | Tables | Views | Columns | PK / FK / UK | Indexes | What it is |
|---|---|---|---|---|---|---|
| **INVENTORY** | 610 | 565 | 14,027 | 106 / 83 / 54 | 133 | The stores/purchase ERP core: items, MRS, indents, enquiries, POs, GRNs, issues, stock, RDC, work orders, planning/budget, approval workflow. Also 283 sequences, 52 triggers, 64 procedures, 40 functions, 23 jobs, 274 Java classes (app logic lives in the DB). |
| **SCM** | 38 | 0 | 1,391 | 16 / 13 / 19 | 37 | Party master (suppliers/customers/agents), geography (COUNTRY/STATE/PLACE), users (RAWUSER), fibre/cotton inward (FIBRE, COTTONGI, AGN), bank details. |
| **HRDNEW** | 22 | 0 | 932 | 9 / 10 / 18 | 34 | HR: STAFF, DEPARTMENT, DESIGNATION, CURRENTATTENDANCE (2.3M rows), SHIFTALLOCATION, OVERTIME, apprentices. |
| **ADMIN** | 15 | 0 | 331 | 8 / 6 / 3 | 13 | Gate/admin: MAINPASS (330k), CASHBANK (581k), DOCUMENT, CAMERAIP, vehicle movement. |
| **INSUR** | 11 | 0 | 198 | 5 / 2 / 2 | 7 | Insurance log book, MBD lock config, MILL. |

Also visible but outside scope: `MAINTENANCE` (referenced by `INVENTORY.MACHINEVIEW`), `PROCESS`, `ALPA`.

Shape of the data:
- 560 of the 565 views are `TEMP*` scratch/report views (e.g. `TEMP1POORDER1207`), one per document/report run; only 5 are real (`SUPPLIER`, `MACHINEVIEW`, `DELETEDMRSVIEW`, `X119991`, `X3339991`).
- 84 tables are `TEMP*`/`*<digits>`/backup copies (`REGPLANTEMP20260812`, `MATDESC0204`…). 40 tables are empty. 30 tables exceed 100k rows.
- **574 of 696 tables have no primary key.** Only 114 FKs exist database-wide; most relationships are enforced by the application (see §4).
- Datatypes: NUMBER 11,860 · VARCHAR2 4,597 · DATE 386 · BLOB 24 · CHAR 6 · LONG RAW 5. **Business dates are mostly `VARCHAR2(8)` `YYYYMMDD` strings** (§5).
- Comments are rare: 14 table comments, 203 column comments (mostly HRDNEW.STAFF/SCHEMEAPPRENTICE and SCM.PARTYMASTER).

## 2. Important tables

### 2.1 Masters

| Table | Rows | Key | Notes |
|---|---|---|---|
| `INVENTORY.INVITEMS` | 41,862 | PK `ITEM_CODE` VARCHAR2(9), always 9 chars, almost never numeric; unique idx on `ITEM_CODE`, non-unique on `ITEM_NAME` | **The item master; 42 inbound FKs.** `ITEM_NAME` VARCHAR2(70): 41,428 distinct, **407 names shared by >1 item** (name resolution can be ambiguous). `OBSOLETE`=1 on 12,087 (29%). `STATUS`/`FLAG`/`ITEM_LOCKSTATUS` are 0 everywhere (no information). `UOMCODE` (47), `STKGROUPCODE` (35), `MATGROUPCODE` (46), `DEPTCODE` (23), `HODCODE` (10). Running totals `OPGQTY/OPGVAL/RECQTY/RECVAL/ISSQTY/ISSVAL`, reorder `ROQ/MINQTY/MAXQTY`, location codes, HSN/GST, motor specs, `PHOTO` BLOB. |
| `SCM.PARTYMASTER` | 20,075 | unique idx `PARTYCODE` VARCHAR2(1020) (1–13 chars, 77% numeric), unique idx `PARTYNAME`, unique `ID`; FKs to `SCM.COUNTRY/STATE/PLACE` | **All parties: suppliers, customers, agents; 19 inbound FKs.** `PARTYNAME` unique (20,074 after upper/trim). `GOODSTYPECODE`: 3 → 9,391 · **2 → 8,503 (supplier)** · 1 → 2,181. The ERP's own `INVENTORY.SUPPLIER` view is `PARTYMASTER WHERE GOODSTYPECODE = 2`; 1,644 of 1,645 distinct PO suppliers have code 2. `CUSTOMERTYPECODE` 1/2, `LOCATIONTYPECODE` (20 values), `PARTYSTATUS` mostly null, `ACCSTATUS` 0/1, `DUPLICATEPARTYSTATUS`=1 on 25. Holds PAN, GSTIN, bank account, contact data — **sensitive; never select these columns**. |
| `INVENTORY.DEPT` | 74 | PK `DEPT_CODE` | Stores departments; `DEPT_NAME`, `MILLCODE`, `GROUPCODE`. (HRDNEW has its own `DEPARTMENT`, 113 rows, different codes.) |
| `INVENTORY.UNIT` | — | `UNIT_CODE` | Referenced by `MRS.UNIT_CODE` (8 units). |
| `SCM.PLACE / STATE / COUNTRY` | 508 / 114 / 76 | codes | Geography for parties. |
| `SCM.RAWUSER` | 2,317 | `USERCODE` | Application users (EMPCODE, unit, auth flags); referenced by approval workflow tables. |
| `HRDNEW.STAFF / DEPARTMENT / DESIGNATION` | 280 / 113 / 331 | `EMPCODE` / `DEPTCODE` / `DESIGNATIONCODE` | HR masters. |

### 2.2 Procure-to-pay transactions (INVENTORY)

| Table | Rows | Date column(s) | Grain / keys | Notes |
|---|---|---|---|---|
| `MRS_TEMP` | 98,205 | `MRSDATE` 2011→today, `DUEDATE` (dirty) | `MRSNO` (29,358) + `SLNO` + `ITEM_CODE`; no PK | **Department MRS creation / approval workflow** (table comment: "Table for Department Mrs Creation"). `APPROVALSTATUS` 1=90,638 / 0=7,567; `REJECTIONSTATUS`, `STORESREJECTIONSTATUS`, `HOLDINGSTATUS`, `READYFORAPPROVAL`, `JMD*`, `REASONFORREJECTIONSTORES`, `ISCONVERTED`, `CONVERTTOORDER`; `ORDERNO/GRNNO` essentially null. 12 triggers (alerts to JMD/SO on rejection/approval, conversion). Has `ITEM_NAME` denormalised. `STOCK`, `MRSPENDING`, `ORDERPENDING` snapshot columns. |
| `MRS` | 177,286 | `MRSDATE` 2007→today | PK `ID`; unique (`MRSNO`,`ITEM_CODE`,`QTY`,`MRSDATE`,`SLNO`); FKs → `INVITEMS`, `DEPT`, `UNIT` | **Approved requisition lines** that link onward: `ENQUIRYNO` (13k), `ORDERNO` (84k), `GRNNO` (79k), `ISSUENO`. `MRSTYPECODE` 0/1/2/3/4, `STATUS` 0/1, `URGORD` urgent flag, `BUDGETSTATUS`, `NETRATE/NETVALUE`, `PLANMONTH`. |
| `INDENT` / `INDENT_REQUEST` | 375,592 / 372 | — | `CODE`→`INVITEMS` | Department indents (issue requests) with `ISSUESTATUS`, `INDENTTYPE`, `CONVERTTOINDENTSTATUS`. |
| `ENQUIRY` / `COMPARSIONENQUIRY` | 94,555 / 47,517 | `ENQDATE` 2002→today | `ENQNO` (19,454), `SUP_CODE`, `MRS_NO`, `ITEM_CODE`; FKs → `PARTYMASTER`, `INVITEMS` | Supplier enquiries/quotations per MRS item; comparison statements with `COMPARSIONAPPROVAL/AUTH/CANCEL`. |
| **`PURCHASEORDER`** | 178,707 | `ORDERDATE` 2007-05-31→2026-09-21 (100% `YYYYMMDD`), `DUEDATE` (1 bad value), `CREATEDDATETIME` `DD.MM.YYYY HH24:MI:SS` text | **One row per PO line**: unique (`ORDERNO`,`ORDERBLOCK`,`SLNO`,`ITEM_CODE`); 83,582 order numbers, 84,290 headers (`ORDERNO`+`ORDERBLOCK`, block 0/1/2/3/4 = mill/unit series); no PK. FKs `ITEM_CODE`→`INVITEMS`, `SUP_CODE`→`PARTYMASTER`. | 1,645 suppliers, 26,058 items, 63 depts. `MRSNO` never null (every PO line traces to an MRS). `QTY`, `RATE` NUMBER(14,4), `NET`, `DISC`, `TAX`, `FREIGHT`, `PF`, `BASIC`, GST cols never null; `QTY` never 0 (`NET/QTY` is the ERP's own "NetRate"). Approval flags `JMDORDERAPPROVAL`, `SOORDERAPPROVAL`, `IAORDERAPPROVAL` = 1 on 99.5%. `ORDERTYPECODE` 0/1/2/3, `ORDERSOURCETYPECODE` 0/1/2/3/null, `GSTSTATUS`, `SPLITSTATUS`, `AMENDED` 0/1/2, `CONVERTED`. **`STATUS` = 0 on all rows and `GRN_PENDINGSTATUS` = 1 on only 7 — neither is a usable "pending" filter.** ~9–12k lines and 4–5k orders per year. `PYORDER` (60k) holds pre-2008 orders; `DELETEDORDER` (13.7k) deleted lines; `ORDERAMEND` amendments. |
| `ADVREQUEST` | 31,888 | `ADVDATE`, `ORDERDATE` | `ORDERNO`, `CODE`, `SUP_CODE` | Advance payment requests against POs. |
| **`GRN`** | 204,115 | `GRNDATE` 2008-04-03→today (100% `YYYYMMDD`); `GATEINDATE`, `INVDATE`, `DCDATE` dirty | `GRNNO` (97,131) + `GRNBLOCK` + `SLNO`; `CODE`→`INVITEMS`, `SUP_CODE`→`PARTYMASTER`; `ORDERNO`, `MRSNO`, `GATEINNO`, `INVNO` | **Goods receipt lines**: `ORDERQTY`, `INVQTY`, `GRNQTY`, `REJQTY`, `PENDING`, `INVRATE`, `INVAMOUNT`, `INVNET`, `GRNVALUE`; `INSPECTION` 1 on 99.9%; `REJFLAG`=1 on 950 with `REJREASON`; `GRNAUTHSTATUS`, `HOLDINGSTATUS`, GST columns. Companion `INVOICEGRN` (76k, 2019→), `WORKGRN` (11.6k, job-work receipts), `GRNHOLDING` (116k), `GRN_UPDATES` (503k audit), `PYGRN` (pre-2008). |
| `GATEINWARDRDC` | 45,841 | `GIDATE` | `GINO`, `SUP_CODE` | Gate inward entries; `TRN_GIREJECTION` (577) rejections at gate. |
| **`ISSUE`** | 413,597 | `ISSUEDATE` 2008-04-01→today (100% `YYYYMMDD`) | `ISSUENO` (177,627) + `SLNO`; `CODE`→`INVITEMS`; `DEPT_CODE` (68), `HODCODE` (17), `UNIT_CODE` (8) | **Stores issues / consumption**: `QTY`, `ISSRATE`, `ISSUEVALUE`, `INDENTTYPE` 0–5, `AUTHENTICATION` 0/1, `RECDSTATUS`, `PARTYSTATUS`+`PARTYCODE` (issue to party), debit-note columns. `NONSTOCKISSUE` (11.6k) for non-stock items; `PYISSUE` pre-2008. |
| **`ITEMSTOCK`** | 43,968 | `ENTRYDATETIME` DATE 2019→today | `ITEMCODE` VARCHAR2(20)→`INVITEMS` (23,648 items), `MILLCODE` (4), `HODCODE` (44); **multiple rows per item** (1–31) | **Current stock per item per mill/HOD**: `STOCK`, `STOCKVALUE`, `INDENTQTY`, `TRANSFERQTY`, `RESERVEDQTY`; never null/negative; 87% of rows are zero. `ITEMSTOCK_UPDATE` (288k) audit. A stock question must SUM across rows per item. |
| `STOCKTRANSFER` / `TRANSFERSTOCK` | 598 / 2,925 | `TRANDATE` / `TRANSFERDATE` | item, from/to mill or user | Inter-mill / inter-user transfers. |
| `RDC` / `RDCMEMO` | 58,528 / 49,041 | `RDCDATE`, `MEMODATE` (dirty), `DUEDATE` | `RDCNO`, `SUP_CODE`, `DEPT_CODE`; `RDC.REQUEST_ID`→`TRN_USER_REQUEST` | Returnable delivery challans (material sent out for repair/job work) and memos. |
| `WORKORDER` / `WORKGRN` | 11,547 / 11,581 | `GRNDATE` | `SUP_CODE` | Job-work orders and their receipts. |
| `REGPLAN`, `REGPOOL`, `YEARLYPLANNING*`, `MONTHLYPLANNINGITEM`, `YEARLYBUDGET` (empty), `BUDGETCARRIEDOVERHISTORY` | up to 110k | — | item | Planning / budgeting. |
| `MS_APPROVAL_WORKFLOW`, `TRN_USER_REQUEST`, `TRN_APPROVAL_DETAILS` | 19 / 202 / 448 | DATE | `USERCODE`→`SCM.RAWUSER` | New (2025-11→) generic approval workflow with `APP_LEVEL`/`APP_GROUP`/`APP_STATUS`. |
| `DELETED*` tables | e.g. `DELETEDMRS` 6.4k | — | same keys as the live table | Trigger-maintained audit copies (`MRS_DELETION_BACKUP` etc.). |

### 2.3 Other schemas

| Table | Rows | Notes |
|---|---|---|
| `HRDNEW.CURRENTATTENDANCE` | 2,296,198 | Daily attendance by employee — the real home of the "attendance for empcode" questions V1 rejects. `SHIFTALLOCATION` 284k, `OVERTIME` 187k. |
| `ADMIN.CASHBANK` / `MAINPASS` / `DOCUMENT` | 581k / 331k / 98k | Accounts cash-bank ledger; gate passes; documents. |
| `ADMIN.CAMERAIP`, `INVENTORY.MS_MATERIALTRANSCAMERAIP` (111), `MS_MATERIALTRANSCONFIG` (31), `ADMIN.TRN_VEHICLEMOVEMENT*` (~1k) | — | Material-transport / vehicle / camera configuration (2025→). |
| `SCM.FIBRE` 110k, `COTTONGI` 43k, `AGN` 13k, `PURCHASE` 2.9k, `SAMPLESDC` 5.4k, `BANKDETAILS` 102k | — | Raw-material (fibre/cotton) inward and party bank data — outside V1. |
| `INSUR.LOGBOOK` 399 | — | Insurance incident log by machine/department. |

## 3. Important columns and conventions

- **Item**: `INVITEMS.ITEM_CODE` (9 chars) is the join key everywhere, but the column is named `ITEM_CODE` in PURCHASEORDER/MRS/ENQUIRY, **`CODE` in GRN/ISSUE/INDENT/ADVREQUEST/NONSTOCKISSUE**, and `ITEMCODE` in ITEMSTOCK/YEARLYPLANNING.
- **Supplier**: `SUP_CODE` VARCHAR2(15) in PURCHASEORDER/GRN/ENQUIRY/RDC/WORKORDER → `PARTYMASTER.PARTYCODE`; `ACCODE` in SCM tables. Supplier display name = `PARTYMASTER.PARTYNAME` (unique). Supplier filter should include `GOODSTYPECODE = 2` to match the ERP's `SUPPLIER` view.
- **Department**: `DEPT_CODE` → `INVENTORY.DEPT.DEPT_CODE` (stores departments, 63–68 used); `HODCODE` = head-of-department code (10–19 values); `UNIT_CODE` (8), `GROUP_CODE` (41–42), `MILLCODE` 0/1/3/4 (mill/unit; 0 and 1 carry 99%).
- **Document numbers** are per-series integers with a block: `ORDERNO`+`ORDERBLOCK`, `GRNNO`+`GRNBLOCK`, `MRSNO`, `ISSUENO`, `ENQNO`, `RDCNO`, plus `SLNO` line serial. `ID` is a surrogate on most tables (unique but rarely a declared PK).
- **Amounts** (NUMBER(12,2)/(14,4)): PO `QTY, RATE, DISC, TAX, NET, BASIC, FREIGHT, PF, OTHERS, CGST/SGST/IGST(+VAL), CESS`; GRN `INVQTY, INVRATE, INVAMOUNT, INVNET, GRNQTY, GRNVALUE, REJQTY, PENDING`; ISSUE `QTY, ISSRATE, ISSUEVALUE`; MRS `QTY, NETRATE, NETVALUE`; ITEMSTOCK `STOCK, STOCKVALUE, RESERVEDQTY`.
- **Status/flag columns are numeric codes**, usually NUMBER(1,0)/(2,0): `0/1` for flags; `APPROVALSTATUS`, `REJECTIONSTATUS`, `HOLDINGSTATUS` 0/1/2; `MRSTYPECODE` 0–4; `INDENTTYPE` 0–5; `ORDERTYPECODE` 0–3. Meanings are not documented in the dictionary — they live in the Java/PL-SQL app. Many `STATUS`/`FLAG`/`POSTED` columns are constant (all 0) and mean nothing.
- **Approval roles** appear as column prefixes: `JMD*` (joint managing director), `SO*` (stores officer), `IA*` (internal audit), `HOD*`, `DEPTMRSAUTH`. "material hold at Store officer" / "approval pending" map to `MRS_TEMP.HOLDINGSTATUS`, `READYFORAPPROVAL`, `APPROVALSTATUS`, `STORESREJECTIONSTATUS`.
- **Audit columns**: `USERCODE`, `CREATIONDATE` (text), `SYSTEMNAME`/`SYSNAME`, `MACADDRESS`, `IPADDRESS`, `SYSTEM_DATE` (DATE), `UPDATED_DATE`.

## 4. Relationships

Declared FKs: 114 total (INVENTORY→INVENTORY 67, INVENTORY→SCM 16, SCM→SCM 13, HRDNEW 10, ADMIN 6, INSUR 2). Hubs:
`INVITEMS` (42 inbound), `PARTYMASTER` (19), `DEPT` (3), `MS_MATERIALTRANSCONFIG` (3), `SCM.RAWUSER` (3).

Declared (verified) join paths that matter for V1:
```
PURCHASEORDER.ITEM_CODE  -> INVITEMS.ITEM_CODE        ISSUE.CODE        -> INVITEMS.ITEM_CODE
PURCHASEORDER.SUP_CODE   -> PARTYMASTER.PARTYCODE     ITEMSTOCK.ITEMCODE-> INVITEMS.ITEM_CODE
GRN.CODE                 -> INVITEMS.ITEM_CODE        MRS.ITEM_CODE     -> INVITEMS.ITEM_CODE
GRN.SUP_CODE             -> PARTYMASTER.PARTYCODE     MRS.DEPT_CODE     -> DEPT.DEPT_CODE
ENQUIRY.ITEM_CODE/SUP_CODE -> INVITEMS / PARTYMASTER  MRS.UNIT_CODE     -> UNIT.UNIT_CODE
INVOICEGRN, ADVREQUEST, RDC, WORKORDER, WORKGRN, GATEINWARDRDC .SUP_CODE -> PARTYMASTER
PARTYMASTER.CITYCODE/STATECODE/COUNTRYCODE -> PLACE / STATE / COUNTRY
```
**Not declared but real (application-enforced, verified by profiling):**
```
PURCHASEORDER.MRSNO  = MRS.MRSNO  (never null; 36,463 distinct)      MRS.ORDERNO = PURCHASEORDER.ORDERNO
GRN.ORDERNO = PURCHASEORDER.ORDERNO,  GRN.MRSNO = MRS.MRSNO           MRS.GRNNO   = GRN.GRNNO
PURCHASEORDER.DEPT_CODE / GRN.DEPT_CODE / ISSUE.DEPT_CODE = DEPT.DEPT_CODE   (no FK)
MRS_TEMP.MRSNO -> MRS.MRSNO after conversion (ISCONVERTED / CONVERTTOORDER)
ENQUIRY.MRS_NO = MRS.MRSNO ; COMPARSIONENQUIRY.ENQNO = ENQUIRY.ENQNO
MRS_TEMP has no FKs at all; ORDERNO/GRNNO on it are (almost) always null
```
The ERP's own report views join `PurchaseOrder INNER JOIN Supplier ON Supplier.Ac_Code = PurchaseOrder.Sup_Code`
(where `SUPPLIER` = `PARTYMASTER` filtered to `GOODSTYPECODE = 2`).

Document flow as the data shows it:
```
Dept creates MRS_TEMP  --approve/convert-->  MRS  --> ENQUIRY (quotes) --> COMPARSIONENQUIRY
   --> PURCHASEORDER (lines; JMD/SO/IA approvals; ADVREQUEST advances)
   --> GATEINWARDRDC / GRN / INVOICEGRN (receipt, inspection, rejection)  --> ITEMSTOCK (on-hand)
   --> INDENT (dept request)  --> ISSUE / NONSTOCKISSUE (consumption)     --> RDC (returnables out)
```

## 5. Data conventions that break naive SQL

1. **Dates are text.** `ORDERDATE`, `MRSDATE`, `ISSUEDATE`, `GRNDATE`, `ENQDATE`, `GIDATE`, `DUEDATE`, `REJDATE`, `ADVDATE`,
   `TRANDATE` are `VARCHAR2(8)` holding `YYYYMMDD` (100% conforming on the main date of each transaction table).
   Some secondary date columns are `NUMBER(8,0)` (`JMDORDERAPPDATE`, `SPJDATE`, `DEBITNOTEDATE`, `TRANSFERDATE`) or
   free text with mixed formats (`INVDATE`, `DCDATE`, `GATEINDATE`, `MRS_TEMP.DUEDATE`, `RDCDATE` has a `'5.8'`).
   `CREATEDDATETIME`/`CREATIONDATE`/`ENTRYDATETIME` are `DD.MM.YYYY HH24:MI:SS` or `YYYYMMDD HH24:MI:SS` text.
   True `DATE` columns are few: `SYSTEM_DATE`, `UPDATED_DATE`, `INSPECTIONDATE`, `ITEMSTOCK.ENTRYDATETIME`,
   `PARTYMASTER.LASTMODIFIED`, the new `TRN_*`/`MS_*` tables.
   With `NLS_DATE_FORMAT = DD-MON-RR`, `ORDERDATE >= TRUNC(SYSDATE) - 30` or `ORDERDATE BETWEEN :d1 AND :d2` (DATE
   binds) raises **ORA-01861**. Correct forms: `ORDERDATE BETWEEN '20260101' AND '20261231'` (string binds) or
   `ORDERDATE >= TO_CHAR(TRUNC(SYSDATE) - 30, 'YYYYMMDD')`; sorting `ORDER BY ORDERDATE DESC` is safe.
2. **Oracle 11.2**: no `FETCH FIRST n ROWS ONLY` (12c syntax); use `ROWNUM` or `ROW_NUMBER() OVER (...)`.
   Identifiers max 30 chars.
3. **No primary keys / surrogate `ID`s**: uniqueness is by document number + block + serial.
4. **Multi-row stock**: `ITEMSTOCK` is per item per mill/HOD; aggregate before answering "stock of X".
5. **Constant status columns** (`STATUS`, `FLAG`, `POSTED` often all 0) must not be used as filters; the meaningful
   states are the workflow columns in §3.
6. **Supplier = party with `GOODSTYPECODE = 2`**; `PARTYMASTER` also holds 11.5k customers/others.
7. **Item names are not unique** (407 duplicates) and 29% of items are obsolete.
8. **Archives**: `PY*` tables hold pre-2008 data; `DELETED*` hold deleted rows; `*_UPDATE(S)`/`*_CHANGES` are audit trails.

## 6. ERP / business concepts represented

| Concept | Where |
|---|---|
| Item / material master, UOM, stock groups, HSN/GST | `INVITEMS`, `UNIT`, `STKGROUP*`, `MATGROUP*` |
| Supplier / party, geography, bank & tax ids | `SCM.PARTYMASTER` (+`SUPPLIER` view), `PLACE/STATE/COUNTRY`, `BANKDETAILS`, `PARTY_*` groups |
| Material requisition & approval workflow (dept → stores → JMD/SO/IA) | `MRS_TEMP`, `MRS`, `MRSITEMS`, `MS_APPROVAL_WORKFLOW`, `TRN_*` |
| Enquiry / quotation / comparison | `ENQUIRY`, `COMPARSIONENQUIRY`, `ENQUIRYSTATUS*` |
| Purchase order (lines, amendments, splits, approvals, GST, EPCG/import, project orders) | `PURCHASEORDER`, `ORDERAMEND`, `EPCG*`, `ADVREQUEST` |
| Goods receipt, gate inward, inspection, rejection, invoice matching | `GATEINWARDRDC`, `GRN`, `INVOICEGRN`, `GRNHOLDING`, `TRN_GIREJECTION` |
| Stock on hand, reservations, transfers | `ITEMSTOCK`, `STOCKTRANSFER`, `TRANSFERSTOCK`, `STOCK_ADJUST` |
| Indent & issue (consumption by department) | `INDENT*`, `ISSUE`, `NONSTOCKISSUE`, `MATERIALINDENT*`, `MATERIALRETURN*` |
| Returnable challans / job work | `RDC`, `RDCMEMO`, `WORKORDER`, `WORKGRN` |
| Planning & budget | `REGPLAN*`, `YEARLYPLANNING*`, `MONTHLYPLANNING*`, `BUDGET*` |
| HR: attendance, shifts, overtime, staff | `HRDNEW.*` |
| Gate pass, cash-bank, vehicles, cameras | `ADMIN.*` |
| Raw fibre / cotton inward | `SCM.FIBRE`, `COTTONGI`, `AGN` |

### 6.1 Business definitions verified from the ERP's own code (safe to catalog)

The INVENTORY schema holds 64 procedures, 40 functions, 52 triggers, 14 types (246k chars of PL/SQL, read via
`ALL_SOURCE`). These give exact, non-inferred meanings for concepts V1 has been rejecting as unverified:

- **order pending approval stage** — `FUNCTION INVENTORY.GETORDERPENDINGSTATUS`: PURCHASEORDER row: SO/IA/JMD all 0 -> pending at 'SO' (stores officer); SO=1,IA=0 -> 'IA' (internal audit); SO=1,IA=1,JMD=0 -> 'JMD'; all 1 -> approved.
- **grn status of order** — `FUNCTION INVENTORY.GETGRNSTATUS`: 1 if any PURCHASEORDER line of the ORDERNO has INVQTY > 0 (something received), else 0.
- **total stock** — `FUNCTION INVENTORY.GETTOTALSTOCK / GETTOTALSTOCKNEW`: SUM(ITEMSTOCK.STOCK) WHERE ITEMCODE = item AND MILLCODE = mill [AND (HODCODE = owner OR HODCODE = 1)]; NULL -> 0.
- **last issue date** — `FUNCTION INVENTORY.GETLASTISSUEDATE`: MAX(ISSUE.ISSUEDATE) WHERE CODE = item AND MILLCODE = mill; stored YYYYMMDD, displayed DD.MM.YYYY.
- **grn rejection reason** — `FUNCTION INVENTORY.GRN_REJECTION_REASON`: distinct non-null GRN.REASON for the GRNNO/MILLCODE.
- **mrs approval conversion** — `TRIGGER INVENTORY.CONVERT_TRG / MSG_STATUS_MRS`: When MRS_TEMP.APPROVALSTATUS changes 0 -> 1 (and not deleted), a row is inserted into MRS (approved requisition); DUEDATE = today + DUEDAYS; an alert 'MRS NO n has been Approved' is queued.
- **mrs store rejection** — `TRIGGER INVENTORY.STOREREJECTION / ON_UPDATE_STOREREJECTION`: MRS_TEMP.STORESREJECTIONSTATUS = 1 stamps STORESREJECTEDDATETIME, sets ENQUIRYSTATUS = 0 and closes related ENQUIRY rows (CLOSESTATUS = 1).
- **supplier** — `VIEW INVENTORY.SUPPLIER`: SCM.PARTYMASTER WHERE GOODSTYPECODE = 2 (name=PARTYNAME, ac_code=PARTYCODE).
- **po net rate** — `ERP report views (TEMP1POORDER*, X3339991)`: PURCHASEORDER.NET / PURCHASEORDER.QTY AS NetRate; QTY is never 0 or null.

Other functions worth reading before cataloguing their concepts: `GETWORKORDERPENDINGSTATUS`, `GETADVREQUESTPENDINGSTATUS`
(same SO/IA/JMD ladder on ADVREQUEST), `GETNONSTOCK`, `GETOTHERSTOCK`, `GETSERVICESTOCK`, `GETBUDGET`,
`GET_PROJECTSOAPPROVAL_PENDING`, `GRN_INWARD_MODE`; procedures `UPDATEITEMSTOCK*`, `TBL_ALERT_MRS_PENDING_SO`,
`TBL_ALERT_PENDING_MRS_JMD`, `TBL_ALERT_MONTHLY_MRS_PENDING`. Tables most used by that code (= the true core):
ITEMSTOCK, PURCHASEORDER, MRS, BUDGET*, DEPT, INVITEMS, MRSUSERAUTHENTICATION, MRS_TEMP, GRN, TBL_ALERT_COLLECTION, INDENT.

### 6.2 Machine-readable companion

`data/oracle_schema_profile_2026-09-22.json` (1.8 MB): every one of the 696 tables with columns/types/nullability,
PK, unique keys, FKs in both directions, indexes, comments, triggers and exact row count; profiles for 58 business
tables (date formats/ranges, key cardinalities, code-value distributions); the 5 real view definitions; the
conventions and verified definitions above. No row data. Intended input for catalog growth and, later, retrieval.

## 7. Impact on AJSMGPT V1 (findings only — nothing changed)

1. **Date handling is wrong for this database.** The catalog marks `ORDERDATE/MRSDATE/DUEDATE/ISSUEDATE` as
   `date_filter`, the datatype validator therefore treats them as DATE, and the generator/validator *require*
   `ADD_MONTHS(TRUNC(SYSDATE), -N)` / `TRUNC(SYSDATE) - N` comparisons and `datetime.date` binds. On this DB every
   such query will fail (ORA-01861) or misbehave. Every relative- and absolute-date question in the 47-question eval
   is affected. Fix direction: a `YYYYMMDD`-text date category in the catalog + validator/generator rules that
   compare as strings (`>= TO_CHAR(..., 'YYYYMMDD')`, string binds), never TO_DATE on the column.
2. **`FETCH FIRST N ROWS ONLY` is not valid on 11.2.** The three current `PASS_PIPELINE` SQLs and the validator's
   required limit syntax would fail at execution. Needs `ROWNUM` / analytic row-numbering.
3. **fix.md #7 confirmed from the DB side**: `SUM(QTY)` beside `ORDERDATE` without `GROUP BY` → ORA-00937.
4. **Supplier resolution should filter `GOODSTYPECODE = 2`** (or at least rank it); `PARTYNAME` is unique, so the
   name path cannot be AMBIGUOUS, but a code path via `PARTYCODE` may collide with `ITEM_CODE`-like values.
5. **Material resolution can genuinely be AMBIGUOUS** (407 duplicate names) and should surface `OBSOLETE`.
6. **Catalog gaps have real columns**: `rate` → `PURCHASEORDER.RATE` (never null) / `GRN.INVRATE`; `cost consumed` →
   `ISSUE.ISSUEVALUE` (+`ISSRATE`); GRN family → `GRN` (`GRNQTY`, `INVQTY`, `REJQTY`, `PENDING`, `GRNDATE`); stock → `ITEMSTOCK`
   summed per item; "order pending" → not a status column (`GRN_PENDINGSTATUS` is 7 rows) but derivable as
   PO lines with no matching GRN; MRS status/approval/rejection → `MRS_TEMP` workflow columns; attendance →
   `HRDNEW.CURRENTATTENDANCE`.
7. **`MRS_TEMP` vs `MRS`**: the catalog's `mrs` family points at `MRS_TEMP` (department creation/approval stage).
   Quantity/history questions arguably belong to `MRS` (approved lines with PO/GRN links); status/approval/rejection
   questions belong to `MRS_TEMP`. Both are needed.
8. **Offline metadata is fresh enough**: 5 new tables, 42 new columns, 2 new FKs since June; all V1 catalog columns
   exist. `data/multi_schema_metadata.json` records datatypes correctly (VARCHAR2(8)) — the catalog role overrides it.
9. **Row limits**: `SQL_MAX_ROWS` on the server .env; PURCHASEORDER alone is 179k lines — ranking/aggregate queries
   are fine, but unbounded detail queries need the limit.
10. **Deployment**: neither server copy is V1; the running service is the legacy `/ask` on `AJSMGPT_git`. V1
    deployment will need the V1 branch, the new `oracle_client.py` (binds, timeouts, error mapping), and ideally a
    SELECT-only account.

## 8. Unclear / needs investigation

- Meaning of numeric code domains (`MRSTYPECODE` 0–4, `INDENTTYPE` 0–5, `ORDERTYPECODE` 0–3, `ORDERSOURCETYPECODE`,
  `LOCATIONTYPECODE`, `GOODSTYPECODE` 1/3, `MILLCODE` 3/4, `ORDERBLOCK`) — not in the dictionary; needs the app
  source or a business owner. Only `GOODSTYPECODE = 2` (supplier) is confirmed from a view definition.
- What `INVITEMS.OPGQTY/RECQTY/ISSQTY` represent (running totals? per year?) vs `ITEMSTOCK.STOCK`.
- Whether "PO pending" should mean `GRN` absent, `GRN.PENDING > 0`, or a `MRS_TEMP`/`MRS` state.
- `ORDERBLOCK`/`GRNBLOCK` semantics (0 = 91% of rows; 1–4 small) — likely unit/mill series.
- `MRS_TEMP.DUEDATE` and several secondary date columns mix `YYYYMMDD`, `D/M/YYYY`, `DD.MM.YYYY`: any date filter on
  them must be regex-guarded.
- `HRDNEW.<UNI_PK>` / `SCM.<SYS_C0014966>` FK targets could not be resolved by name (constraint on a table outside
  the visible set).
- The 5 tables with ORA-01031 (`SCM.COTTONAGN`, `SCM.PURCHASEAGN`, …) are listed in the dictionary but not selectable.
- Who else writes to these tables (23 INVENTORY jobs, 52 triggers): stock figures may lag; `ITEMSTOCK.STOCKUPDATE`
  flag suggests a batch refresh.
- Server `.env` `ORACLE_SCHEMAS` / `ALLOWED_SCHEMAS` value and whether the legacy service's schema allowlist matches
  the five studied here.

## 9. Appendix — every table with row count (live, 2026-09-22)


### INVENTORY — 610 tables (557 regular, 53 TEMP*/dated copies)

`SPIN` 838910, `GRN_UPDATES` 503196, `ISSUE` 413597, `INDENT` 375592, `INDENTRECEIVERLIST` 289839, `ITEMSTOCK_UPDATE` 287889, `MRS_TEMP_CHANGES` 225656, `GRN` 204115, `MATDESC` 178731, `PURCHASEORDER` 178707, `BSABSTRACT` 177501, `MRS` 177286, `MATERIALRETURNDETAILS` 160412, `MATERIALINDENTDETAILS` 158545, `MATERIALINDENTRETURNDETAILS` 156813, `PURCHASEORDER_UPDATE` 140668, `GRNHOLDING` 115782, `ORDERAMEND` 112154, `MATDESCAMEND` 111749, `REGPLANTEMP` 110805, `MRS_TEMP` 98205, `ENQUIRY` 94555, `INDENTMULTIGROUPALLOCATE` 92429, `INDENTMULTIGROUPALLOCATEDET` 91421, `BUDGETCARRIEDOVERHISTORY` 88546, `REGPLAN` 80188, `INVOICEGRN` 76384, `MATERIALSCRABDETAILS` 72699, `PYORDER` 60421, `RDC` 58528, `PYISSUE` 55098, `RDCMEMO` 49041, `COMPARSIONENQUIRY` 47517, `FIBRE` 46250, `GATEINWARDRDC` 45841, `ITEMSTOCK` 43968, `PYMATDESC` 43953, `INVITEMS` 41862, `PYGRN` 39308, `PLANPOOLTEMP` 38414, `RESERVATION` 36925, `ACCGRNRECEIPTDETAILS` 36393, `TOSENDMAIL` 36281, `WORKGRN_UPDATES` 35127, `ADVREQUEST` 31888, `INVITEMS_HISTORY` 26386, `C11` 24441, `HSNCODES` 22166, `MELANGEINVITEMS` 21483, `C2` 20149, `C10` 17986, `C12` 17986, `C3` 17484, `DYEINGINVITEMS` 14473, `C1` 14426, `DELETEDCOMPARSIONENQUIRY` 13849, `DELETEDORDER` 13750, `DELETEDMATDESC` 13720, `PARTYBANKACCOUNTMAILHISTORY` 13431, `SERVICESTOCKTRANSFER` 13280, `SERVICESTOCKISSUE` 12500, `RDCRECEIVER` 11626, `WORKGRN` 11581, `NONSTOCKISSUE` 11557, `WORKORDER` 11547, `OUTPASS` 10880, `INDENTAUTHREMOVALDETAILS` 10250, `RDCSTATUS` 10064, `MRS_TEMP_DETAILS` 9938, `STOREMATERIAL_MOVEMENT_PAYMENT` 9725, `STORESSTOCKCHECKINGDAILY` 9465, `SUBSTOREBSABSTRACT` 8795, `DELETEDADVREQUEST` 8435, `MONTHLYPLANNINGITEM` 6679, `DELETEDMRS` 6444, `D_INDENT` 6358, `D_MATERIALINDENTRETURNDETAILS` 6353, `D_MATERIALINDENTDETAILS` 6352, `INWARDMODE` 6266, `INDENTDELETIONLOG` 6262, `ITEMSTOCK_HISTORY` 6052, `C2A` 5986, `OUTPASSDETAILS` 5927, `STOCK` 5714, `BKP_MATERIALINDENTRETURNDET` 5492, `BKP_INDENT` 5466, `BKP_MATERIALINDENTDETAILS` 5461, `INDENTMODIFICATIONLOG` 5429, `INDENTMATERIAL_INTERNALSERVICE` 5308, `ITEMSTOCK_TRANSFER` 5027, `INVOICEREMAINDERMAILDC` 4885, `BIN` 4805, `BKP_MATERIALRETURNDETAILS` 4803, `DELETEDINVITEMS` 4738, `DELETEDMELANGEINVITEMS` 4655, `PROJECTSKELETON` 4589, `MODIFIEDRDCMEMO` 4452, `INDENT_PREISSUEDSCRAP` 4325, `INDENTBUDGETCARRIEDOVERHISTORY` 4286, `INVOICEREMAINDERMAIL` 4151, `D_MATERIALRETURNDETAILS` 3842, `MRSITEMS` 3811, `MATERIALINDENTDETAILS_TEMP` 3671, `PURCHASEORDERSTATUS` 3524, `MS_BIN` 3360, `BINCHANGE_HISTORY` 3275, `WHATSAPP_GRN_PENDING_LIST` 3226, `PARTYBANKACCOUNTDETAILS` 3166, `WORKGRNHOLDING` 3108, `YEARLYPLANNING` 3081, `WORKORDERAMEND` 3072, `TRANSFERSTOCK` 2925, `NONRETURNITEMSCHECKING` 2811, `GREENTAPEDETAILS` 2806, `GREENTAPERECEIVED` 2603, `GREENTAPEDAILYAUTHENTICATION` 2597, `MACHINEHISTORYVERIFY` 2594, `GRNINSPECTIONDETAILS` 2387, `RPHISTORYTEMP` 2196, `DELETEDTEMPREGPOOL` 2080, `POOLINGCOMPLETED` 2070, `DELETEDGRN` 2050, `RPJINVITEMS` 1991, `REQUESTAUTHDETAILS` 1985, `HSNGSTRATE` 1980, `ADVANCEREQUESTSTATUS` 1950, `OLD_MATERIALRETURNDETAILS` 1855, `GRN_REQUEST` 1847, `REQUESTAUTH` 1704, `MULTIHSN` 1699, `GREENTAPEAUTHENTICATION` 1625, `PRERECEIPTSCRAPTRANSFER` 1566, `DELETEDRDC` 1509, `KAMADHENUINVITEMS` 1464, `BUDGET` 1445, `MRS_DELETION_HISTORY` 1418, `BUDGET_INDENT` 1405, `BUDGET_SERVICE` 1383, `DIV1FASUPPLIER` 1362, `BUDGETBK` 1361, `MATERIALRETURNDETAILS_TEMP` 1335, `ISSUE_UPDATE` 1247, `TOUPDATEORDERUSERCODE` 1191, `BKP_INTERNALSERVICE` 1141, `COMPARSIONENQUIRYREMOVAL` 1101, `USERCATALIST` 1063, `DELETEDINDENT` 1036, `DELETEDREGPLANTEMP` 1026, `RDCOUTWARD_PENDINGREASON` 931, `PURCHASEORDERHISTORY` 918, `YEARLYPLANNINGTEMP` 909, `GREENTAPEISSUE` 892, `ISSUERECEIVERLIST` 872, `STOCK_ADJUST_DETAILS` 821, `DYESSELECTIONINFO` 676, `HSNCODECHANGES` 658, `BKREGPOOLTEMP` 634, `MATERIAL_PROPERTIES_NEW` 624, `DELAYEDINVOICEENTRYREQAPPROVAL` 623, `DIRECTORDERITEMS` 611, `STOCKTRANSFER` 598, `TRN_GIREJECTIONDETAILS` 588, `DELETEDWORKORDER` 581, `TRN_GIREJECTION` 577, `ORDERTAXCHANGE` 560, `DELETEDISSUE` 558, `YEARLYPLANNINGITEM` 529, `MODIFIEDRDC` 477, `TRN_APPROVAL_DETAILS` 448, `SCRAPRDC` 426, `DELETEDDYEINGINVITEMS` 421, `PROJECTMRSTYPES` 397, `MRS_CHANGES` 388, `ENQUIRYSTATUSANDMAIL` 384, `REMOVENONSTOCK` 374, `INDENT_REQUEST` 372, `REGPOOLTEMP` 366, `MRSREASON` 353, `SERVICBUDGETCARRIEDOVERHISTORY` 343, `DELAYEDINVOICEENTRYREQUEST` 330, `ADVREQUESTHISTORY` 318, `MONTHLYITEMREMOVAL` 316, `GREENTAPEPAYMENT` 309, `GENERALMACHINE` 304, `INDENTUSERRECEIVERLIST` 277, `STOREMATERIAL_AUTHENTICATION` 271, `DEPTBUDGET` 258, `DELAYEDINVENTRYREQAPPROVAL` 252, `BUDGETREQUEST` 234, `STORESSTOCKCHECKINGDAILY_LOG` 229, `DYEINGISSUERECEIVERLIST` 225, `ADVREQUEST_UPDATE` 223, `TRN_USER_REQUEST_DLS` 222, `G1` 218, `PENDINGINVOICECHECK` 212, `USERDEPARTMENTCODELIST` 204, `TRN_USER_REQUEST` 202, `MATERIAL_PROPERTIES` 186, `DELAYEDINVOICEENTRYREQUESTDOC` 185, `PRODUCTIONCOMPLETE` 178, `BUDGETCARRIEDOVER` 176, `INDENTAUTHENTICATION` 168, `D_INTERNALSERVICE` 165, `REGPOOLTEMPBK` 165, `DELAYEDINVENTRYREQUESTDOC` 155, `MRSUSERAUTHENTICATION` 154, `ASSETTRACKING` 152, `DELAYEDINVENTRYREQUEST` 144, `GRNDAILYUPDATION` 140, `ADVREQUEST_RECEIPTDETAILS` 139, `PARTY_MSME_DETAILS` 130, `USERUNITCONTROL` 124, `DELETEDGATEINWARD` 119, `STORESSTOCKCHECKING` 119, `GRNAUTHTIMEREQUEST` 118, `INDENTRETURNGROUPTYPE` 118, `VENKATINVITEMS` 113, `MS_MATERIALTRANSCAMERAIP` 111, `DIRECTRATEHISTORY` 105, `DAILY_STOCK_CONFIG` 103, `REMOVEINDENTPRIORITYDETAILS` 100, `STORESSTOCKCHECKING_LOG` 99, `REMOVEINDENTPRIORITY` 97, `AJSMINVB2BDETAILS` 91, `BACKGATEINWARD` 91, `MATERIALSCRABDETAILS_BKP` 86, `USERDIVISIONPERMISSION` 86, `GREENTAPEPAYMENTVOUCHER` 85, `USERAUTHCONFIG` 83, `SUBSTORESTOCK` 80, `M10` 78, `REGPLANTEMPBK` 75, `DEPT` 74, `RPHISTORY` 74, `RDCDETAILS_IMAGES` 71, `MATGROUP` 70, `STOCK_ADJUST` 70, `DELETE_SERVICESTOCKTRANSFER` 68, `RDCDETAILS` 66, `MRSNOCONFIG` 62, `ABOLISHEDITEMS` 61, `C6` 61, `GSTUOM` 61, `ITEMCHECKTABLES` 58, `WORKORDERHISTORY` 58, `PARTY_MSME_DETAILS_UPDATE` 57, `SCHEDULESERVICEMRSLOG` 53, `INDENTSECONDARYAUTHENTICATION` 51, `MS_MATERIALTRANSAPPUSER` 51, `UOM` 51, `BSNAT` 50, `DELETEDKAMADHENUINVITEMS` 46, `STORESINVOICE` 46, `CATA` 43, `STOREMATERIALPAYEMENTVOUCHER` 43, `INDENTRETURNGROUPALLOCATION` 42, `INVOICEDEBITNOTE` 42, `TPHISTORYTEMP` 42, `BOOKTHRO` 41, `NONST` 41, `CONCERNTRANSFER` 39, `MATDESCTEMP` 39, `DELETEDBUDGET` 38, `DELETED_MONTHLYPLANNINGITEM` 38, `ITEMSTOCKTRANSFER_TEMP` 38, `STOCKGROUP` 38, `PROJECTCONFIG` 36, `DELETEDINDENTAUTHENTICATION` 34, `ENQUIRYSTATUSMASTER` 34, `EPCGLICENCEDETAILS` 33, `ENQUIRYCHANGES` 32, `DELETEDWORKGRN` 31, `MS_MATERIALTRANSCONFIG` 31, `BUDGET_INDENT_BKP` 30, `EPCGLICENCEITEMS` 30, `GRNADJ` 30, `SCRAPRETURNUSERDETAILS` 28, `UPDATEITEMCODE` 26, `DELETED_RDCMEMO` 25, `SCRAPRECEIPT_BKP` 25, `YEAR` 25, `CHANGEITEMLIST` 24, `DELETEDSERVICESTOCKISSUE` 24, `INVPERIOD` 22, `MACHINETYPE` 22, `NEWCHANGEITEMLIST` 22, `HOD` 20, `RDC_JMD_APPROVAL` 20, `JAVA$OPTIONS` 19, `MS_APPROVAL_WORKFLOW` 19, `INVLOGIN` 18, `RDCMEMO_TEST` 17, `BUDGETCARRIEDOVERHISTORY_BKP` 16, `DELETEDNONSTOCKISSUE` 16, `EPCGCONFIG` 16, `MS_GIAPPROVALUSERCONFIG` 16, `Y1` 16, `INWTYPE` 15, `MATERIALINDENTRETURNTYPE` 15, `CONFIG010` 14, `CONFIG011` 14, `CONFIG012` 14, `CONFIG013` 14, `CONFIG014` 14, `CONFIG015` 14, `CONFIG016` 14, `CONFIG017` 14, `CONFIG018` 14, `CONFIG019` 14, `CONFIG020` 14, `CONFIG021` 14, `CONFIG06` 14, `CONFIG07` 14, `CONFIG08` 14, `CONFIG09` 14, `CONFIG110` 14, `CONFIG111` 14, `CONFIG112` 14, `CONFIG113` 14, `CONFIG114` 14, `CONFIG115` 14, `CONFIG116` 14, `CONFIG117` 14, `CONFIG118` 14, `CONFIG119` 14, `CONFIG120` 14, `CONFIG121` 14, `CONFIG16` 14, `CONFIG17` 14, `CONFIG18` 14, `CONFIG19` 14, `CONFIG36` 14, `CONFIG37` 14, `CONFIG38` 14, `CONFIG39` 14, `CONFIG46` 14, `CONFIG47` 14, `CONFIG48` 14, `CONFIG49` 14, `CONFIG57` 14, `CONFIG612` 14, `CONFIG613` 14, `CONFIG68` 14, `CONFIG69` 14, `INDENTDEFAULTUNITDEPTMACHINE` 14, `CONFIG05` 13, `CONFIG15` 13, `CONFIG35` 13, `CONFIG45` 13, `DYESPROPERTIES` 13, `IND_GROUP_CONFIG_AUTO_INSERT` 13, `C4` 12, `ITEM_BRAND` 12, `MONTHS` 12, `TAXPER` 12, `BOOKTO` 11, `BRAND` 10, `BUDGETREVISIONREQ` 10, `DELETED_SERVICESTOCKTRANSFER` 10, `DEPTITEMCONFIG` 10, `GRN_SPJ_REJECTION_REASON` 10, `SHADEWISEDYES` 10, `TM` 10, `GOTSREMARKS` 9, `INVSORT` 9, `PARTYMAILTYPE` 9, `PARTY_MASTER_DATA` 9, `TRIALHOLDING` 9, `DYESANDCHEMICALSINCENTIVE` 8, `INVITEMS_MASTERTABLES_TABLE` 8, `M11` 8, `MATERIALRECEIPTAREA` 8, `OBSOLETEITEMHISTORY` 8, `TINDENT` 8, `TTT` 8, `UNIT` 8, `MS_PAYMENT_TERM_DTL` 7, `RPJINVB2BDETAILS` 7, `TEXAMPLE` 7, `GREENTAPEPAYMENTBK` 6, `GRNMODIFY` 6, `INDENTTYPE` 6, `M1` 6, `M2` 6, `M3` 6, `M4` 6, `M5` 6, `M6` 6, `M7` 6, `MS_SCREEN_MASTER` 6, `NATCONCERNTRANSFER` 6, `NATISSUE` 6, `NATMILL` 6, `OTHERDEPTAUTHENTICATION` 6, `REDTAGISSUEDETAILS` 6, `REDTAGSTOCKDETAILS` 6, `WORKSHOPUSEDMATERIALGROUP` 6, `CHEMICALSPLACEMASTER` 5, `CONFIGALL` 5, `EPCGCURRENCYTYPE` 5, `EPCG_LICENCE_CHARGES` 5, `GREENTAPERATE` 5, `GRNAUTHADDRESS` 5, `INDUSTRYSECTOR` 5, `ITEMSTOCKHISTORY_TEMPO` 5, `ITEMTYPE` 5, `ITEM_INDUSTRY_SECTOR` 5, `ITEM_INDUSTRY_SECTOR_SUBGROUP` 5, `ITEM_MACHINE_MODEL` 5, `MS_GIREJECTIONREMARKS` 5, `MS_PAYMENT_TERMS` 5, `MS_SERVICE_TYPE` 5, `ORDBLOCK` 5, `PAPERSET` 5, `SUPPLIERTYPE` 5, `USERAUTHSCREEN` 5, `INDUSTRYSUBSECTOR` 4, `INVITEMS_MASTER_DATA` 4, `ITEMTABLES` 4, `MAINTENANCEUSERUNITCONTROL` 4, `MS_APPROVESTATUS` 4, `MS_MSME_TYPE` 4, `ORDERSOURCE` 4, `ORDERTYPE` 4, `PARTY_MACRO_SECTOR` 4, `PORT` 4, `S1` 4, `STFORM` 4, `AREAWISELOADINGAUTHENTICATION` 3, `BACKUPPURCHASEORDER` 3, `BKP_INDRETURNGROUPALLOCATION` 3, `CONDITIONLESTYPE` 3, `CREATE$JAVA$LOB$TABLE` 3, `DELAYEDINVENTRYTYPE` 3, `EPCGLICENCE` 3, `EPCGSTATE` 3, `GSTTYPES` 3, `INVOICEREMINDERSTATUS` 3, `MILL` 3, `MRSTYPE` 3, `MS_GITRANSACTIONTYPE` 3, `MS_MATERIALTRANSDOCTYPE` 3, `MS_MATERIALTRANSGROUPCONFIG` 3, `NATURE` 3, `ORDERPENDINGMASTER` 3, `PAPERSIDE` 3, `REMOVEINDENTPRIORITYUSERS` 3, `REVERSECHARGEPURCHASE` 3, `SCRAPISSUEDETAILS` 3, `SHIFTINGTYPE` 3, `TAXCATEGORY` 3, `TAXTYPE` 3, `DELETEDINVOICEREMAINDERMAIL` 2, `DELETEDINVOICEREMAINDERMAILDC` 2, `DELETEDMRS_TEMP` 2, `DYESLABSTATUS` 2, `DYESPROPERTIESREMARK` 2, `EPCGEXPENSECALPERCENTS` 2, `EPCGORDERTYPE` 2, `EPCG_LICENCE_GROUP` 2, `GREENTAPECONTRACTOR` 2, `INDENTRAISABLEITEM` 2, `INVITEMS_STDANDARDQTY` 2, `INVOICECONFIG` 2, `INVOICETYPE` 2, `ITEMSTOCK_TEMP` 2, `JMDREMARKS` 2, `MATRACK` 2, `MRSINDENTTIMECONTROL` 2, `NAT` 2, `NATRDC` 2, `ORDERCONVERSIONTYPE` 2, `POOL` 2, `PREFERENCE` 2, `PRERECMATERIALINDENTDETAILS` 2, `PRERECMATERIALRETURNDETAILS` 2, `QUALITYPERIODICAL` 2, `STGROUP` 2, `STOCK_ADJUST_DETAILS_LOG` 2, `USERGROUP` 2, `ADVGROUPNO` 1, `AGECONFIG` 1, `AJSMINVB2BCREDITNOTE` 1, `AUTOMRSANDORDERCONVERSIONRATE` 1, `BUDGETPASSWORD` 1, `COSTINGTYPE` 1, `DELETEDENQUIRY` 1, `DELETEDRDCDETAILS` 1, `DYES_PARTY` 1, `INDENTMULTIGROUPDEFAULTSETTING` 1, `INDENTNOCONFIG` 1, `ISSUEPUNCHSTATUS` 1, `K1` 1, `K3` 1, `MATBOX` 1, `MATCOLUMN` 1, `MATERIALINDENTDAYSCONFIG` 1, `MATERIALINDENTDAYSLIMIT` 1, `MATERIALINDENTLOCK` 1, `MATROOM` 1, `MATROW` 1, `MATTRAY` 1, `MERGINGITEMS` 1, `MONTHLYMRSFINALDATE` 1, `MONTHLYPLANNINGCONFIG` 1, `MS_GRNAUTHTIMECONFIG` 1, `MS_PERFORMANCECARD_CONTROL` 1, `MY` 1, `PARTY_DEPARTMENT_GROUP` 1, `POGROUPNO` 1, `RATEGROUPNO` 1, `RATEPERIODICAL` 1, `RDC_TEST` 1, `STOCK_CONFIG` 1, `T` 1, `TESTTRG` 1, `TGATEINWARDRDC` 1, `TIMECONTROL` 1, `TIMECONTROLTYPE` 1, `TMRS` 1, `TT` 1, `WORKGROUPNO` 1, `WORKORDER_UPDATES` 1, `C5` 0, `DELETEDITEMS` 0, `DISCHARGABILITYREMARKS` 0, `DYESPRICEREVISION` 0, `DYES_INVITEMS` 0, `EPCGGROUPEXPENSEDETAILS` 0, `EPCGMATDESC` 0, `EPCGORDERGROUP` 0, `EPCGREQUESTORDER` 0, `GRNREJECTION` 0, `ITEMUSERCONTROL` 0, `ITEM_SUBGROUP` 0, `K2` 0, `MATERIALREDTAGDETAILS` 0, `MICROSOFTDTPROPERTIES` 0, `MOHANRAJ` 0, `MRSITEMCODECHANGELOG` 0, `MRSREMARKS` 0, `OEKOTEXREMARKS` 0, `PARTY_BRAND_GROUP` 0, `PARTY_INDUSTRY_SECTOR_GROUP` 0, `PARTY_INDUSTRY_SUBSECTOR_GROUP` 0, `PARTY_ITEM_TYPE_GROUP` 0, `PARTY_MACHINE_MODEL_GROUP` 0, `PARTY_TYPE_GROUP` 0, `RDC_ITEMSTOCK` 0, `REACHREMARKS` 0, `REGPOOL` 0, `RPJINVB2BCREDITNOTE` 0, `SACGSTRATE` 0, `TPHISTORY` 0, `TRN_GIREJECTION_ATTACHMENT` 0, `WORKINGSCHEDULE` 0, `YEARLYBUDGET` 0


Scratch/dated copies: `TEMPINDENT` 197115, `MATDESC0204` 113472, `REGPLANTEMP20260812` 110563, `NONSTOCKISSUE20230105` 8248, `PYGRN2005` 6574, `TEMPRDC` 4374, `TEMPACCINFO` 3492, `BUDGET20221219` 1427, `BUDGET_INDENT20221219` 1387, `PRERECEIPTSCRAPTRANSFER230626` 353, `ADVTEMP220702` 270, `X2` 135, `TEMP1` 120, `RDCDETAILS20260504` 67, `TEMPGATE` 56, `TEMPPENDMRSORDER1` 49, `TEMP1PENDMRSORDER` 47, `TEMPPENDMRSORDER11` 47, `X118881` 47, `TEMP0` 41, `CONFIG2009` 31, `CONFIG30082010` 31, `TEMP4` 30, `TEMPMRSORDER` 30, `TEMP2` 29, `TEMP3` 29, `TEMPRDC1` 27, `TEMP1MRSITEM` 26, `REGPLANTEMP3885` 25, `ENQUIRY_19228` 24, `ENQUIRY_18472` 20, `TEMP1MRSORDER` 16, `TEMP2MRSORDER` 16, `TEMPMRSORDER22` 16, `X1` 16, `PURCHASEORDER20230711` 14, `TEMPDYESPROPERTIES` 13, `REGPLANTEMP2885` 12, `CHANGEITEMLIST20160716` 10, `IN57905` 8, `ISSUE_DELETE_DYE_20220526` 8, `RDCDELETE2909` 8, `ENQUIRY4866` 4, `MRS_FIREWOORD_20200905` 3, `L92615` 2, `TEMPERATURE` 2, `TEMPMRS` 2, `TEMPWORKGRN` 2, `TEMPWORKORDER` 2, `TEMPGRN` 1, `WG180087` 1, `TEMPREGPOOL` 0, `X3` 0



### SCM — 38 tables (38 regular, 0 TEMP*/dated copies)

`FIBRE` 110483, `BANKDETAILS` 102501, `TBL_ALERT_COLLECTION` 97505, `GIFIBREDETAILS` 56253, `COTTONGI` 43399, `PARTYMASTER` 20075, `AGN` 13231, `ISSUETOSALES` 10615, `SAMPLESDC` 5391, `PURCHASE` 2857, `FIBREBASE` 2325, `RAWUSER` 2317, `YARNGI` 2279, `RECYCLEFIBRE` 1529, `WASTEGI` 1045, `RECYCLE_PURCHASEDETAILS` 771, `RECYCLE_PURCHASE` 757, `PLACE` 508, `GIGATENEWDETAILS` 358, `STATE` 114, `OTHERITEMS` 101, `RECYCLE_CLOTHPURCHASE` 77, `COUNTRY` 76, `RECYCLE_CLOTHPURCHASEDETAILS` 75, `RAWMATERIALTYPE` 41, `PARTYMASTERTDSCONFIG` 22, `SQCMODELMASTER` 16, `PARTYCONTACTSTAFFDATA` 14, `FIBRETYPE` 12, `CURRENCYRATE` 6, `PARTYCONTACTTYPE` 5, `GSTPARTYTYPE` 4, `MAILSENDERSTATUS` 1, `PARTYADDRESSDATA` 1, `COTTONAGN` n/a, `MICROSOFTDTPROPERTIES` 0, `PURCHASEAGN` n/a, `TBL_ALERT_RECIPIENT_COLLECTION` n/a



### HRDNEW — 22 tables (22 regular, 0 TEMP*/dated copies)

`CURRENTATTENDANCE` 2296198, `SHIFTALLOCATION` 283674, `OVERTIME` 187197, `EXTERNALMOVEMENT` 42909, `CANTEENATTENDANCE` 6790, `RELIVEEMP` 4617, `MEDICALCONSUMPTIONISSUE` 1549, `SCHEMEAPPRENTICE` 1098, `MEDICALCONSUMPTIONRECEIPT` 543, `CONTRACTWORKMASTER` 367, `DESIGNATION` 331, `STAFF` 280, `CONTRACTAPPRENTICE` 180, `DEPARTMENT` 113, `PENDINGCHECKLIST` 104, `HODDEPTAUTHENTICATION` 70, `CONTRACTOR` 34, `CONTRACTWORKGROUPMASTER` 13, `STATUS` 10, `CONTRACTDEPARTMENT` 7, `APPRENTICE1` n/a, `MICROSOFTDTPROPERTIES` 0



### ADMIN — 15 tables (15 regular, 0 TEMP*/dated copies)

`CASHBANK` 581116, `MAINPASS` 330555, `DOCUMENT` 97837, `C1` 38799, `VIDEOSESSIONDETAILS` 3200, `ONETOUCHEMPLOYEE` 1726, `TRN_VEHICLEMOVEMENT_DETAILS` 1363, `TRN_VEHICLEMOVEMENT_AUTH` 1326, `TRN_VEHICLEMOVEMENT` 971, `CAMERAIP` 140, `VIDEOSESSION_DOCTYPE` 8, `VIDEOSESSION_SERVER_CONFIG` 5, `TRN_VEHICLEMOVEMENT_TIMEEXTREQ` 3, `GEOPHOTO` n/a, `MICROSOFTDTPROPERTIES` 0



### INSUR — 11 tables (11 regular, 0 TEMP*/dated copies)

`POLICYACKNOWLEDGEMENT` 1260, `LOGBOOK` 399, `TRN_MBD_CLAIM_STATUS` 286, `SURVEYOR` 27, `POLICYTYPE` 22, `POLICY` 14, `POLICYCLAIMSTATUS` 7, `MILL` 5, `MS_MBD_LOCK_CONFIG` 1, `PRELIMINARY` 1, `MICROSOFTDTPROPERTIES` 0

-- table need to use MRS, invitems-master,supplier-master,purchaseorder-transaction.

SELECT owner, table_name
FROM all_tables
WHERE owner IN ('ADMIN', 'HRDNEW', 'INSUR', 'INVENTORY')
ORDER BY owner, table_name;

SELECT owner,
       table_name,
       column_id,
       column_name,
       data_type,
       data_length,
       nullable
FROM all_tab_columns
WHERE owner IN ('ADMIN', 'HRDNEW', 'INSUR', 'INVENTORY')
AND UPPER(table_name) IN (
'AGECONFIG','BREAKDOWNEVENTS','BREAKDOWNS','BUDGET','BUDGET_INDENT',
'BUDGETCARRIEDOVER','BUDGETCARRIEDOVERHISTORY','BUDGETMODIFY',
'BUDGETREQUEST','BUDGETREVISIONREQ','CATA','CONFIG','DELETEDMRS',
'DEPARTMENT','DEPT','DEPTBUDGET','DEPTITEMCONFIG','DOCUMENT',
'DYEINGINVITEMS','ENQUIRY','GATEINWARD','GATEINWARDRDC','GENERALMACHINE',
'GRN','HOD','INDENTBUDGETCARRIEDOVERHISTORY','INVITEMS','ISSUE',
'ITEMSTOCK','MACHINE','MACHINETYPE','MATERIALINDENTDAYSCONFIG',
'MATERIALINDENTRETURNTYPE','MATGROUP','MILL','MONTHLYPLANNINGITEM',
'MRS','MRS_TEMP','MRS_TEMP_DETAILS','MRSNOCONFIG','MRSREASON','MRSTYPE',
'MRSUSERAUTHENTICATION','NATURE','NEWSERVICES','OBSOLETEITEMHISTORY',
'ORDBLOCK','ORDERTYPE','OTHERDEPTAUTHENTICATION','POLICY',
'PRERECEIPTSCRAPTRANSFER','PROCESSINGUNIT','PROCESSUNITS','PROJECTCONFIG',
'PROJECTMRSTYPES','PROJECTSKELETON','PURCHASEORDER','PYORDER','RAWUSER',
'RDC','RDCMEMO','RDCRECEIVER','REGPOOL','RPJINVITEMS','SCRAPRDC',
'SERVICEMACHINE','STOCKGROUP','SUPPLIER','TBL_ALERT_COLLECTION',
'TEMPMRSORDER','TRANSFERSTOCK','TRIALHOLDING','TRN_MBD_CLAIM_STATUS',
'UNIT','UOM','USERDEPARTMENTCODELIST','USERUNITCONTROL','WORKGRN',
'YEARLYBUDGET'
)
ORDER BY owner, table_name, column_id;


SELECT
    acc.owner,
    acc.table_name,
    acc.column_name,
    ac.constraint_name,
    ac.constraint_type
FROM all_constraints ac
JOIN all_cons_columns acc
    ON ac.owner = acc.owner
   AND ac.constraint_name = acc.constraint_name
WHERE ac.owner IN ('ADMIN', 'HRDNEW', 'INSUR', 'INVENTORY')
AND ac.table_name IN (
'DOCUMENT','DEPARTMENT','POLICY','TRN_MBD_CLAIM_STATUS',
'AGECONFIG','BUDGET','BUDGETCARRIEDOVER','BUDGETCARRIEDOVERHISTORY',
'BUDGETREQUEST','BUDGETREVISIONREQ'
)
ORDER BY acc.owner, acc.table_name, ac.constraint_type, acc.position;

SELECT
    fk.owner AS fk_owner,
    fk.table_name AS fk_table,
    fk_cols.column_name AS fk_column,
    pk.owner AS ref_owner,
    pk.table_name AS ref_table,
    pk_cols.column_name AS ref_column,
    fk.constraint_name
FROM all_constraints fk
JOIN all_cons_columns fk_cols
    ON fk.owner = fk_cols.owner
   AND fk.constraint_name = fk_cols.constraint_name
JOIN all_constraints pk
    ON fk.r_owner = pk.owner
   AND fk.r_constraint_name = pk.constraint_name
JOIN all_cons_columns pk_cols
    ON pk.owner = pk_cols.owner
   AND pk.constraint_name = pk_cols.constraint_name
   AND fk_cols.position = pk_cols.position
WHERE fk.constraint_type = 'R'
AND fk.owner IN ('ADMIN', 'HRDNEW', 'INSUR', 'INVENTORY')
ORDER BY fk.owner, fk.table_name, fk.constraint_name, fk_cols.position;


SELECT
    ORDERNO,
    ORDERDATE,
    ITEM_CODE,
    QTY,
    INVQTY,
    STATUS,
    GRN_PENDINGSTATUS
FROM INVENTORY.PURCHASEORDER
WHERE NVL(GRN_PENDINGSTATUS, 0) <> 0;

-- admin - admin, HRDNew-hrdnew,insur-insur,SCM - rawmat 

SELECT ID, ORDERNO, ORDERDATE, SUP_CODE, ITEM_CODE, QTY, RATE, NET, STATUS, GRN_PENDINGSTATUS FROM INVENTORY.PURCHASEORDER;


SELECT ITEMCODE, STOCK FROM INVENTORY.STOCK;
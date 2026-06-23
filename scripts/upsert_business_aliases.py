import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.ollama_client import get_embedding

load_dotenv(".env")

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION")

ALIASES = [
    {
        "id": 200001,
        "text": "Business alias: supplier details, vendor details, party master, supplier code, vendor code, party code. Main table is SCM.PARTYMASTER. Related inventory purchase tables use SUP_CODE to join SCM.PARTYMASTER.PARTYCODE.",
        "schema": "SCM",
        "table": "PARTYMASTER",
        "full_table_name": "SCM.PARTYMASTER",
        "type": "business_alias",
    },
    {
        "id": 200002,
        "text": "Business alias: pending purchase orders, pending PO, purchase order status, supplier purchase order, item wise purchase order. Main tables are INVENTORY.PURCHASEORDER, INVENTORY.ORDERPENDINGMASTER, INVENTORY.PURCHASEORDERSTATUS. Purchase order item code joins INVENTORY.INVITEMS.ITEM_CODE.",
        "schema": "INVENTORY",
        "table": "PURCHASEORDER",
        "full_table_name": "INVENTORY.PURCHASEORDER",
        "type": "business_alias",
    },
    {
        "id": 200003,
        "text": "Business alias: current stock, item stock, available stock, material balance, inventory balance. Main tables are INVENTORY.ITEMSTOCK, INVENTORY.STOCK, INVENTORY.INVITEMS.",
        "schema": "INVENTORY",
        "table": "ITEMSTOCK",
        "full_table_name": "INVENTORY.ITEMSTOCK",
        "type": "business_alias",
    },
    {
        "id": 200004,
        "text": "Business alias: employee department, staff department, employee designation. Main tables are HRDNEW.STAFF, HRDNEW.DEPARTMENT, HRDNEW.DESIGNATION. STAFF.DEPTCODE joins DEPARTMENT.DEPTCODE.",
        "schema": "HRDNEW",
        "table": "STAFF",
        "full_table_name": "HRDNEW.STAFF",
        "type": "business_alias",
    },
    {
        "id": 200005,
        "text": "Business alias: insurance claim status, pending insurance claim, MBD claim, claim intimation. Main tables are INSUR.TRN_MBD_CLAIM_STATUS, INSUR.LOGBOOK, INSUR.POLICYACKNOWLEDGEMENT.",
        "schema": "INSUR",
        "table": "TRN_MBD_CLAIM_STATUS",
        "full_table_name": "INSUR.TRN_MBD_CLAIM_STATUS",
        "type": "business_alias",
    },
        {
        "id": 200006,
        "text": "Business alias: material requisition, MRS, material request, pending MRS approval, department material request. Main tables are INVENTORY.MRS, INVENTORY.MRS_TEMP, INVENTORY.TRN_APPROVAL_DETAILS, INVENTORY.MS_APPROVAL_WORKFLOW.",
        "schema": "INVENTORY",
        "table": "MRS",
        "full_table_name": "INVENTORY.MRS",
        "type": "business_alias",
    },
        {
        "id": 200007,
        "text": "Business rule: pending purchase orders should use INVENTORY.PURCHASEORDER as the main table. Useful columns are ID, ORDERNO, ORDERDATE, SUP_CODE, ITEM_CODE, QTY, RATE, NET, STATUS, GRN_PENDINGSTATUS. INVENTORY.PURCHASEORDER.ID can join INVENTORY.PURCHASEORDERSTATUS.ORDERID when status history is needed. Avoid assuming STATUS='Pending' unless confirmed.",
        "schema": "INVENTORY",
        "table": "PURCHASEORDER",
        "full_table_name": "INVENTORY.PURCHASEORDER",
        "type": "business_alias",
    },
        {
        "id": 200008,
        "text": "Business rule: supplier details should use SCM.PARTYMASTER. Useful real columns are PARTYCODE, PARTYNAME, ADDRESS1, ADDRESS2, ADDRESS3, OFFPHONE1, OFFPHONE2, EMAIL, EMAIL1, CONTPERSON1, PERPHONE1, GSTINID, PANNO, PARTYSTATUS. Do not use NAME, ADDRESS, CONTACT because these columns do not exist.",
        "schema": "SCM",
        "table": "PARTYMASTER",
        "full_table_name": "SCM.PARTYMASTER",
        "type": "business_alias",
    },
    {
        "id": 200009,
        "text": "Business rule: employee department should use HRDNEW.STAFF joined with HRDNEW.DEPARTMENT. Join HRDNEW.STAFF.DEPTCODE = HRDNEW.DEPARTMENT.DEPTCODE. Useful real STAFF columns are EMPCODE, EMPNAME, DEPTCODE, DESIGNATIONCODE, UNITCODE, DOJ, STAFFTYPE. Useful DEPARTMENT columns are DEPTCODE, DEPTNAME, HRDNAME, ACTIVESTATUS. Do not use DEPTNAME from STAFF because STAFF does not have DEPTNAME.",
        "schema": "HRDNEW",
        "table": "STAFF",
        "full_table_name": "HRDNEW.STAFF",
        "type": "business_alias",
    },
        {
        "id": 200010,
        "text": "Strict SQL rule for employee department: never select DEPTNAME from HRDNEW.STAFF. Correct SQL pattern is SELECT s.EMPCODE, s.EMPNAME, s.DEPTCODE, d.DEPTNAME FROM HRDNEW.STAFF s JOIN HRDNEW.DEPARTMENT d ON s.DEPTCODE = d.DEPTCODE. DEPTNAME exists only in HRDNEW.DEPARTMENT.",
        "schema": "HRDNEW",
        "table": "STAFF",
        "full_table_name": "HRDNEW.STAFF",
        "type": "business_alias",
    },
        {
        "id": 200011,
        "text": "Strict SQL rule for material requisition pending approval: INVENTORY.MRS is the main table. Useful real MRS columns are ID, MRSNO, MRSDATE, ITEM_CODE, ITEMNAME, QTY, UNIT_CODE, DEPT_CODE, ORDERNO, STATUS, DEPTMRSAUTH, MRSAUTHUSERCODE, ENTRYDATETIME, REMARKS, REQUESTREMARKS. INVENTORY.TRN_APPROVAL_DETAILS has RECORD_ID, SCREEN_ID, USERCODE, APP_LEVEL, APP_GROUP, APP_STATUS, APP_DATE, ACTION, REMARKS. APP_STATUS is NUMBER, never compare APP_STATUS to text like 'Pending'. Do not invent joins unless relationship context confirms it. If status code meaning is unknown, do not filter; show approval/status columns only.",
        "schema": "INVENTORY",
        "table": "MRS",
        "full_table_name": "INVENTORY.MRS",
        "type": "business_alias",
    },
        {
        "id": 200012,
        "text": "Business rule: pending material requisition / pending MRS should use INVENTORY.MRS_TEMP as main table. Join INVENTORY.MRS_TEMP.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE, INVENTORY.MRS_TEMP.DEPT_CODE = INVENTORY.DEPT.DEPT_CODE, INVENTORY.MRS_TEMP.GROUP_CODE = INVENTORY.CATA.GROUP_CODE using LEFT JOIN, INVENTORY.MRS_TEMP.UNIT_CODE = INVENTORY.UNIT.UNIT_CODE, and LEFT JOIN INVENTORY.MRS ON INVENTORY.MRS.MRSNO = INVENTORY.MRS_TEMP.MRSNO AND INVENTORY.MRS.SLNO = INVENTORY.MRS_TEMP.SLNO. Pending condition: MRS_TEMP.MRSFLAG = 1, NVL(MRS.ORDERNO,0)=0, MRS_TEMP.REJECTIONSTATUS=0, MRS_TEMP.STORESREJECTIONSTATUS=0, MRS_TEMP.ITEMDELETE=0, MRS_TEMP.ISDELETE=0, and MILLCODE should be 0 or null. The column name is MILLCODE, not MILL_CODE. Useful columns: MRSDATE, MRSNO, ITEM_CODE, INVITEMS.ITEM_NAME, QTY, UNIT.UNIT_NAME, DEPT.DEPT_NAME, CATA.GROUP_NAME, DUEDATE, ID, REMARKS, REASONFORMRS, DOCUMENTID, DEPTMRSAUTH, READYFORAPPROVAL, APPROVALSTATUS, STORESREJECTIONSTATUS, REJECTIONSTATUS, ITEMDELETE, ISDELETE.",
        "schema": "INVENTORY",
        "table": "MRS_TEMP",
        "full_table_name": "INVENTORY.MRS_TEMP",
        "type": "business_alias",
    },
]

ALIASES.append(
    {
        "id": 200013,
        "text": (
            "Business rule: pending purchase order by material name or item name. "
            "When user asks pending order, pending PO, open purchase order, or purchase pending "
            "for a material name like keyboard, do NOT compare INVENTORY.PURCHASEORDER.ITEM_CODE directly "
            "to the material name. Keyboard is an item/material name, not item code. "
            "Use INVENTORY.PURCHASEORDER joined with INVENTORY.INVITEMS on "
            "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE. "
            "Filter material name using UPPER(INVENTORY.INVITEMS.ITEM_NAME) LIKE '%KEYBOARD%' "
            "or replace KEYBOARD with the user supplied material name. "
            "Useful purchase order columns are ORDERNO, ORDERDATE, SUP_CODE, ITEM_CODE, QTY, RATE, NET, STATUS, GRN_PENDINGSTATUS. "
            "Useful item columns are ITEM_CODE and ITEM_NAME. "
            "If pending quantity column INVQTY exists in PURCHASEORDER context, pending condition can be "
            "NVL(PURCHASEORDER.QTY,0) > NVL(PURCHASEORDER.INVQTY,0). "
            "If INVQTY is not confirmed, do not invent pending status meaning; show STATUS and GRN_PENDINGSTATUS."
        ),
        "schema": "INVENTORY",
        "table": "PURCHASEORDER",
        "full_table_name": "INVENTORY.PURCHASEORDER",
        "type": "business_alias",
    }
)


ALIASES.append(
    {
        "id": 200014,
        "text": (
            "CRITICAL STRICT SQL RULE for pending purchase order by material/item name: "
            "When user asks pending order, pending PO, open purchase order, purchase pending, or pending order for item like fabric, yarn, button, thread, keyboard, "
            "use INVENTORY.PURCHASEORDER joined with INVENTORY.INVITEMS on PURCHASEORDER.ITEM_CODE = INVITEMS.ITEM_CODE. "
            "Filter item/material name only using UPPER(INVENTORY.INVITEMS.ITEM_NAME) LIKE '%MATERIAL_NAME%'. "
            "Never compare INVENTORY.PURCHASEORDER.STATUS with text like 'Pending'. STATUS is NUMBER. "
            "Never compare INVENTORY.PURCHASEORDER.GRN_PENDINGSTATUS with text like 'Pending'. GRN_PENDINGSTATUS is NUMBER. "
            "Do not use STATUS = 'Pending', GRN_PENDINGSTATUS = 'Pending', STATUS IS NULL, or GRN_PENDINGSTATUS IS NULL for pending PO unless exact numeric status code is confirmed. "
            "If numeric pending status meaning is unknown, do not filter by STATUS or GRN_PENDINGSTATUS. Only display STATUS and GRN_PENDINGSTATUS as output columns."
        ),
        "schema": "INVENTORY",
        "table": "PURCHASEORDER",
        "full_table_name": "INVENTORY.PURCHASEORDER",
        "type": "business_alias",
    }
)

def main():
    client = QdrantClient(url=QDRANT_URL)

    points = []
    for item in ALIASES:
        vector = get_embedding(item["text"])
        points.append(
            models.PointStruct(
                id=item["id"],
                vector=vector,
                payload=item
            )
        )

    client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    print(f"Upserted {len(points)} business aliases into {QDRANT_COLLECTION}")

if __name__ == "__main__":
    main()
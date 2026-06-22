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
]

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

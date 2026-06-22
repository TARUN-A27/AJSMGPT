import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.ollama_client import get_embedding

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION")


INVENTORY_METADATA = [
    {
        "id": 1,
        "schema": "INVENTORY",
        "table": "PURCHASEORDER",
        "text": """
        INVENTORY.PURCHASEORDER stores purchase order details.
        Used for pending purchase orders, supplier orders, item-wise purchase orders,
        ordered quantity, purchase order status, PO date, PO number, and purchase workflow.
        Related to INVENTORY.INVITEMS using ITEM_CODE.
        Do not join SCM.PARTYMASTER now because only INVENTORY access is available.
        """
    },
    {
        "id": 2,
        "schema": "INVENTORY",
        "table": "INVITEMS",
        "text": """
        INVENTORY.INVITEMS stores inventory item master details.
        Used for item code, item name, item description, material name,
        inventory item details, item category, and item classification.
        Related to PURCHASEORDER, GRN, ITEMSTOCK, MRS, ISSUE, TRANSFERSTOCK.
        """
    },
    {
        "id": 3,
        "schema": "INVENTORY",
        "table": "ITEMSTOCK",
        "text": """
        INVENTORY.ITEMSTOCK stores current stock balance and available quantity.
        Used for stock balance, current stock, item-wise stock, material availability,
        store stock, inventory quantity, and stock reports.
        Related to INVENTORY.INVITEMS using ITEMCODE.
        """
    },
    {
        "id": 4,
        "schema": "INVENTORY",
        "table": "GRN",
        "text": """
        INVENTORY.GRN stores goods receipt note information.
        Used for received material, GRN details, supplier receipt, item receipt,
        accepted quantity, rejected quantity, receipt date, and purchase receipt tracking.
        Related to INVENTORY.INVITEMS using CODE.
        """
    },
    {
        "id": 5,
        "schema": "INVENTORY",
        "table": "MRS",
        "text": """
        INVENTORY.MRS stores material requisition slip details.
        Used for department material requests, pending material requests,
        requested items, MRS reports, unit-wise requests, and department-wise requests.
        Related to INVENTORY.INVITEMS, INVENTORY.UNIT, and INVENTORY.DEPT.
        """
    },
    {
        "id": 6,
        "schema": "INVENTORY",
        "table": "ISSUE",
        "text": """
        INVENTORY.ISSUE stores material issue details.
        Used for issued materials, store issue, department issue, item issue,
        issue quantity, issue date, and material consumption.
        Related to INVENTORY.INVITEMS using CODE.
        """
    },
    {
        "id": 7,
        "schema": "INVENTORY",
        "table": "TRANSFERSTOCK",
        "text": """
        INVENTORY.TRANSFERSTOCK stores stock transfer details.
        Used for item transfer, store transfer, stock movement,
        transfer quantity, transfer date, and location-wise movement.
        Related to INVENTORY.INVITEMS using ITEM_CODE.
        """
    },
    {
        "id": 8,
        "schema": "INVENTORY",
        "table": "SUPPLIER",
        "text": """
        INVENTORY.SUPPLIER stores supplier or vendor master details if available.
        Used for supplier name, vendor details, supplier code, purchase supplier,
        and vendor-wise reports within INVENTORY schema.
        Use this table only if it exists and has required supplier columns.
        """
    },
    {
        "id": 9,
        "schema": "INVENTORY",
        "table": "DEPT",
        "text": """
        INVENTORY.DEPT stores inventory department master details.
        Used for department code, department name, department-wise material request,
        department-wise issue, and inventory department reports.
        Related to MRS using DEPT_CODE.
        """
    },
    {
        "id": 10,
        "schema": "INVENTORY",
        "table": "UNIT",
        "text": """
        INVENTORY.UNIT stores unit master details.
        Used for unit code, unit name, department unit, and unit-wise reports.
        Related to MRS using UNIT_CODE.
        """
    }
]


def main():
    client = QdrantClient(url=QDRANT_URL)

    points = []

    for item in INVENTORY_METADATA:
        vector = get_embedding(item["text"])

        points.append(
            models.PointStruct(
                id=item["id"],
                vector=vector,
                payload={
                    "schema": item["schema"],
                    "table": item["table"],
                    "text": item["text"].strip()
                }
            )
        )

    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=points
    )

    print(f"Inserted {len(points)} INVENTORY metadata records into Qdrant")


if __name__ == "__main__":
    main()

import os
import sys
import json
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

INPUT_FILE = Path("data/inventory_schema_metadata.json")


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Metadata file not found: {INPUT_FILE}")

    metadata = json.loads(INPUT_FILE.read_text(encoding="utf-8"))

    client = QdrantClient(url=QDRANT_URL)

    points = []

    for index, item in enumerate(metadata, start=1):
        text = item["text"]
        vector = get_embedding(text)

        points.append(
            models.PointStruct(
                id=index,
                vector=vector,
                payload={
                    "schema": item["schema"],
                    "table": item["table"],
                    "full_table_name": item["full_table_name"],
                    "columns": item["columns"],
                    "text": text,
                }
            )
        )

        print(f"Prepared: {item['full_table_name']}")

    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=points
    )

    print("=" * 80)
    print(f"Upserted {len(points)} extracted INVENTORY metadata records into Qdrant.")
    print(f"Collection: {QDRANT_COLLECTION}")


if __name__ == "__main__":
    main()

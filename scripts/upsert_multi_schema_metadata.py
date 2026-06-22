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

load_dotenv(".env")

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION")

INPUT_FILE = Path("data/multi_schema_metadata.json")
BATCH_SIZE = 25


def main():
    metadata = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    client = QdrantClient(url=QDRANT_URL)

    total = len(metadata)
    print(f"Total metadata records: {total}")
    print(f"Collection: {QDRANT_COLLECTION}")

    batch = []

    for index, item in enumerate(metadata, start=1):
        vector = get_embedding(item["text"])

        batch.append(
            models.PointStruct(
                id=index,
                vector=vector,
                payload={
                    "schema": item["schema"],
                    "table": item["table"],
                    "full_table_name": item["full_table_name"],
                    "columns": item["columns"],
                    "text": item["text"],
                }
            )
        )

        if len(batch) >= BATCH_SIZE:
            client.upsert(collection_name=QDRANT_COLLECTION, points=batch)
            print(f"Upserted {index}/{total}")
            batch = []

    if batch:
        client.upsert(collection_name=QDRANT_COLLECTION, points=batch)
        print(f"Upserted {total}/{total}")

    print("Done.")


if __name__ == "__main__":
    main()

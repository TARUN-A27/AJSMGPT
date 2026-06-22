import os
import sys
import json
import time
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
BATCH_SIZE = 10
MAX_TEXT_CHARS = 6000


def compact_text(item):
    columns = item.get("columns", [])

    col_names = []
    for col in columns:
        name = col.get("column_name")
        dtype = col.get("data_type")
        if name:
            col_names.append(f"{name} {dtype}")

    text = f"""
Schema: {item['schema']}
Table: {item['full_table_name']}
Columns: {", ".join(col_names)}
Rules: SELECT only. Use full schema.table names.
""".strip()

    return text[:MAX_TEXT_CHARS]


def embed_with_retry(text, retries=3):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            return get_embedding(text)
        except Exception as e:
            last_error = e
            print(f"Embedding failed attempt {attempt}/{retries}: {e}")
            time.sleep(3)

    raise last_error


def main():
    metadata = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    client = QdrantClient(url=QDRANT_URL)

    total = len(metadata)
    print(f"Total metadata records: {total}")
    print(f"Collection: {QDRANT_COLLECTION}")

    batch = []

    for index, item in enumerate(metadata, start=1):
        text = compact_text(item)
        vector = embed_with_retry(text)

        batch.append(
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

        if len(batch) >= BATCH_SIZE:
            client.upsert(collection_name=QDRANT_COLLECTION, points=batch)
            print(f"Upserted {index}/{total}")
            batch = []
            time.sleep(1)

    if batch:
        client.upsert(collection_name=QDRANT_COLLECTION, points=batch)
        print(f"Upserted {total}/{total}")

    print("Done.")


if __name__ == "__main__":
    main()

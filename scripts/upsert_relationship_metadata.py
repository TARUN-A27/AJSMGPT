import json
import os
import sys
import time
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

INPUT_FILE = Path("data/schema_relationships.json")
BATCH_SIZE = 25
START_ID = 100000
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def make_text(entry: dict) -> str:
    return (
        f"Relationship: {entry['from_schema']}.{entry['from_table']}.{entry['from_column']} "
        f"joins to {entry['to_schema']}.{entry['to_table']}.{entry['to_column']} "
        f"using constraint {entry['constraint_name']}.")


def load_relationships(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Relationship file not found: {path}")

    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of relationship objects")

    print(f"Loaded {len(data)} relationships from {path}")
    return data


def upsert_batch(client: QdrantClient, points: list[models.PointStruct], batch_index: int) -> None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client.upsert(collection_name=QDRANT_COLLECTION, points=points)
            print(f"Upserted batch {batch_index} ({len(points)} points)")
            return
        except Exception as exc:
            print(f"Warning: upsert batch {batch_index} failed on attempt {attempt}/{MAX_RETRIES}")
            print(exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS * attempt)
            else:
                raise


def main() -> int:
    if not INPUT_FILE.exists():
        print(f"ERROR: Input file missing: {INPUT_FILE}")
        return 1

    if not QDRANT_URL or not QDRANT_COLLECTION:
        print("ERROR: QDRANT_URL or QDRANT_COLLECTION is not set in environment")
        return 1

    relationships = load_relationships(INPUT_FILE)
    client = QdrantClient(url=QDRANT_URL)

    points = []
    total = len(relationships)
    print(f"Preparing {total} relationship metadata points for Qdrant collection {QDRANT_COLLECTION}")

    for index, entry in enumerate(relationships, start=0):
        try:
            text = make_text(entry)
            vector = get_embedding(text)

            payload = {
                "type": "relationship",
                "from_schema": entry["from_schema"],
                "from_table": entry["from_table"],
                "from_column": entry["from_column"],
                "to_schema": entry["to_schema"],
                "to_table": entry["to_table"],
                "to_column": entry["to_column"],
                "constraint_name": entry["constraint_name"],
                "text": text,
            }

            points.append(
                models.PointStruct(
                    id=START_ID + index,
                    vector=vector,
                    payload=payload,
                )
            )

        except Exception as exc:
            print(f"ERROR: Failed to create embedding for relationship index {index}")
            print(exc)
            return 1

        if len(points) >= BATCH_SIZE:
            batch_index = (index // BATCH_SIZE) + 1
            upsert_batch(client, points, batch_index)
            points = []

    if points:
        batch_index = (total // BATCH_SIZE) + 1
        upsert_batch(client, points, batch_index)

    print(f"Successfully inserted or updated {total} relationship records into Qdrant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.ollama_client import get_embedding

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION")


def main():
    question = "show pending purchase orders"

    client = QdrantClient(url=QDRANT_URL)
    vector = get_embedding(question)

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        limit=5,
        with_payload=True
    )

    results = response.points

    print("Question:", question)
    print("=" * 80)

    for i, result in enumerate(results, start=1):
        payload = result.payload or {}

        print(f"\nRank {i}")
        print("Score:", result.score)
        print("Schema:", payload.get("schema"))
        print("Table:", payload.get("table"))
        print("Context:", payload.get("text"))


if __name__ == "__main__":
    main()

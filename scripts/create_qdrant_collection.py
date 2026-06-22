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


def main():
    client = QdrantClient(url=QDRANT_URL)

    sample_vector = get_embedding("AJSMGPT INVENTORY schema metadata")
    vector_size = len(sample_vector)

    collections = client.get_collections().collections
    names = [c.name for c in collections]

    if QDRANT_COLLECTION in names:
        print(f"Collection already exists: {QDRANT_COLLECTION}")
        return

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=models.Distance.COSINE
        )
    )

    print(f"Created collection: {QDRANT_COLLECTION}")
    print(f"Vector size: {vector_size}")


if __name__ == "__main__":
    main()

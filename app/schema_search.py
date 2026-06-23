import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient

from app.ollama_client import get_embedding

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION")


IMPORTANT_COLUMN_KEYWORDS = [
    "ORDER",
    "PO",
    "DATE",
    "ITEM",
    "CODE",
    "NAME",
    "QTY",
    "RATE",
    "STATUS",
    "SUP",
    "DEPT",
    "UNIT",
    "GRN",
    "MRS",
    "ISSUE",
    "STOCK",
    "PENDING",
    "APPROVAL",
    "AUTH",
    "NET",
    "VALUE",
    "AMOUNT",
]


def search_schema_context(question: str, limit: int = 5, collections: list[str] | None = None) -> list[dict]:
    """
    Search Qdrant for relevant schema metadata and relationships.
    This only searches metadata, not Oracle business data.
    """

    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    client = QdrantClient(url=QDRANT_URL)
    question_vector = get_embedding(question)

    collection_names = collections or [QDRANT_COLLECTION]
    results = []

    for collection in collection_names:
        if not collection:
            continue
        response = client.query_points(
            collection_name=collection,
            query=question_vector,
            limit=limit,
            with_payload=True,
        )

        for point in response.points:
            payload = point.payload or {}
            results.append({
                "score": point.score,
                "schema": payload.get("schema"),
                "table": payload.get("table"),
                "full_table_name": payload.get("full_table_name"),
                "type": payload.get("type"),
                "constraint_name": payload.get("constraint_name"),
                "columns": payload.get("columns") or [],
                "text": payload.get("text"),
            })

    results.sort(key=lambda item: item.get("score", 0), reverse=True)
    return results


def get_column_names(columns: list[dict]) -> list[str]:
    names = []

    for col in columns:
        name = col.get("column_name")
        if name:
            names.append(name)

    return names


def pick_important_columns(columns: list[dict], max_columns: int = 30) -> list[str]:
    """
    Pick compact useful columns from a large table.
    Does not modify Oracle.
    """

    picked = []

    for col in columns:
        name = col.get("column_name", "")
        upper_name = name.upper()

        if any(keyword in upper_name for keyword in IMPORTANT_COLUMN_KEYWORDS):
            picked.append(name)

    # Keep original order and remove duplicates
    seen = set()
    unique_picked = []

    for name in picked:
        if name not in seen:
            unique_picked.append(name)
            seen.add(name)

    return unique_picked[:max_columns]


def build_compact_context_text(results: list[dict], max_tables: int = 5) -> str:
    """
    Compact context for SQL generation.
    Good for Qwen prompt.
    """

    context_parts = []

    for index, item in enumerate(results[:max_tables], start=1):
        columns = item.get("columns") or []
        all_column_names = get_column_names(columns)
        important_columns = pick_important_columns(columns)
        item_type = item.get("type") or "table_metadata"

        if item_type == "relationship":
            context_parts.append(
                f"""
Context Rank: {index}
Type: relationship
Text: {item.get('text')}

Rules:
- Oracle access is SELECT only.
- Use allowed schemas only.
- Always use full table names like INVENTORY.PURCHASEORDER.
""".strip()
            )
            continue

        if item_type == "business_alias":
            context_parts.append(
                f"""
Context Rank: {index}
Type: business_alias
Table: {item.get('full_table_name')}
Alias notes:
{item.get('text')}

Rules:
- Oracle access is SELECT only.
- Use allowed schemas only.
- Always use full table names like INVENTORY.PURCHASEORDER.
""".strip()
            )
            continue

        context_parts.append(
            f"""
Context Rank: {index}
Score: {item.get("score")}
Table: {item.get("full_table_name")}

Important columns:
{", ".join(important_columns)}

All available columns:
{", ".join(all_column_names)}

Rules:
- Oracle access is SELECT only.
- Use only schemas allowed by ORACLE_SCHEMAS and only tables shown in this context.
- Always use full table names like INVENTORY.PURCHASEORDER.
""".strip()
        )

    return "\n\n" + ("\n" + "-" * 80 + "\n").join(context_parts)


if __name__ == "__main__":
    question = "show pending purchase orders"

    results = search_schema_context(question, limit=5)

    print("Question:", question)
    print("=" * 100)

    for item in results:
        print("\nTable:", item["full_table_name"])
        print("Score:", item["score"])

    print("\n" + "=" * 100)
    print("Compact Context for Qwen:")
    print(build_compact_context_text(results))

import os
import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen3:14b")


def get_embedding(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")

    response = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={
            "model": OLLAMA_EMBED_MODEL,
            "prompt": text
        },
        timeout=60
    )

    response.raise_for_status()
    data = response.json()

    if "embedding" not in data:
        raise RuntimeError(f"No embedding returned: {data}")

    return data["embedding"]


def chat_with_qwen(system_prompt: str, user_prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.1
            }
        },
        timeout=180
    )

    response.raise_for_status()
    data = response.json()

    message = data.get("message", {})
    content = message.get("content")

    if not content:
        raise RuntimeError(f"No chat response returned: {data}")

    return content


if __name__ == "__main__":
    vector = get_embedding("INVENTORY PURCHASEORDER table stores purchase order details")
    print("Embedding success")
    print("Vector size:", len(vector))

    reply = chat_with_qwen(
        "You are AJSMGPT. Reply with only one sentence.",
        "Say AJSMGPT is ready."
    )
    print("Chat success")
    print(reply)

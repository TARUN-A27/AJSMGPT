import os
import requests
import oracledb
from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "").rstrip("/")

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")


def check_ollama():
    print("\n[1] Checking Ollama...")
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=10)
        response.raise_for_status()

        data = response.json()
        models = [m.get("name") for m in data.get("models", [])]

        print("✅ Ollama connected")
        print("Models:", models)

    except Exception as e:
        print("❌ Ollama connection failed")
        print("Error:", e)


def check_qdrant():
    print("\n[2] Checking Qdrant...")
    try:
        client = QdrantClient(url=QDRANT_URL)
        collections = client.get_collections()

        print("✅ Qdrant connected")
        print("Collections:", [c.name for c in collections.collections])

    except Exception as e:
        print("❌ Qdrant connection failed")
        print("Error:", e)


def init_oracle_client():
    print("Oracle client lib dir:", ORACLE_CLIENT_LIB_DIR)

    if ORACLE_CLIENT_LIB_DIR:
        oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)
    else:
        oracledb.init_oracle_client()


def check_oracle():
    print("\n[3] Checking Oracle...")
    try:
        if not ORACLE_USER or not ORACLE_PASSWORD or not ORACLE_DSN:
            raise ValueError(
                "Oracle env values missing. Check ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN."
            )

        init_oracle_client()

        print("Oracle driver thin mode:", oracledb.is_thin_mode())

        connection = oracledb.connect(
            user=ORACLE_USER,
            password=ORACLE_PASSWORD,
            dsn=ORACLE_DSN
        )

        cursor = connection.cursor()

        cursor.execute("SELECT USER FROM DUAL")
        current_user = cursor.fetchone()[0]

        cursor.execute("""
            SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
            FROM DUAL
        """)
        current_schema = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM USER_TABLES
        """)
        table_count = cursor.fetchone()[0]

        print("✅ Oracle connected")
        print("Current user:", current_user)
        print("Current schema:", current_schema)
        print("Tables visible in current user:", table_count)

        cursor.close()
        connection.close()

    except Exception as e:
        print("❌ Oracle connection failed")
        print("Oracle driver thin mode:", oracledb.is_thin_mode())
        print("Error:", e)


if __name__ == "__main__":
    print("AJSMGPT connection check started")

    check_ollama()
    check_qdrant()
    check_oracle()

    print("\nConnection check completed")

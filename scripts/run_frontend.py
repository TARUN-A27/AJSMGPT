import subprocess
import threading
import time
import webbrowser

URL = "http://172.16.2.145:8000"


def open_browser():
    time.sleep(2)
    try:
        subprocess.Popen(["google-chrome", URL])
    except FileNotFoundError:
        try:
            subprocess.Popen(["google-chrome-stable", URL])
        except FileNotFoundError:
            webbrowser.open(URL)


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()

    subprocess.run([
        "uvicorn",
        "app.api:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        "8000"
    ])

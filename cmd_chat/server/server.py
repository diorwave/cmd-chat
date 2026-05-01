import time
import multiprocessing
from typing import Optional
from .factory import create_app


def _server_worker(host: str, port: int, password: str) -> None:
    app = create_app(password=password)
    app.run(host=host, port=port, single_process=True, debug=False, access_log=False)


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    password: Optional[str] = None,
    workers: int = 1,
    join_as: Optional[str] = None,
) -> None:
    if join_as:
        _run_with_client(host, port, password or "", join_as)
    else:
        app = create_app(password=password or "")
        app.run(host=host, port=port, single_process=True, debug=False, access_log=True)


def _run_with_client(host: str, port: int, password: str, username: str) -> None:
    import requests
    from cmd_chat.client.client import Client

    proc = multiprocessing.Process(
        target=_server_worker,
        args=(host, port, password),
        daemon=True,
    )
    proc.start()

    client_host = "127.0.0.1" if host == "0.0.0.0" else host
    base_url = f"http://{client_host}:{port}"

    for _ in range(20):
        try:
            requests.get(f"{base_url}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    Client(server=client_host, port=port, username=username, password=password, is_host=True).run()
    proc.terminate()
    proc.join(timeout=1)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=1)

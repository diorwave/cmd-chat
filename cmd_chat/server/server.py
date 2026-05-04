import time
import multiprocessing
from typing import Optional
from .factory import create_app


def _server_worker(host: str, port: int, password: str) -> None:
    app = create_app(password=password)
    app.run(host=host, port=port, single_process=True, debug=False, access_log=False)


def _start_ngrok(port: int, token: Optional[str] = None) -> Optional[str]:
    try:
        from pyngrok import ngrok, conf
        if token:
            conf.get_default().auth_token = token
        tunnel = ngrok.connect(port, "http")
        # https://xxxx.ngrok.io → xxxx.ngrok.io
        return tunnel.public_url.replace("https://", "").replace("http://", "")
    except Exception as e:
        print(f"[ngrok] Failed to start tunnel: {e}")
        return None


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    password: Optional[str] = None,
    workers: int = 1,
    join_as: Optional[str] = None,
    ngrok: bool = False,
    ngrok_token: Optional[str] = None,
) -> None:
    if join_as:
        _run_with_client(host, port, password or "", join_as, ngrok=ngrok, ngrok_token=ngrok_token)
    else:
        ngrok_addr: Optional[str] = None
        if ngrok:
            ngrok_addr = _start_ngrok(port, ngrok_token)

        app = create_app(password=password or "")

        if ngrok_addr:
            _pwd = password or ""
            _addr = ngrok_addr

            @app.after_server_start
            async def _show_ngrok_panel(app, loop):
                _print_ngrok_panel(_addr, _pwd)

        app.run(host=host, port=port, single_process=True, debug=False, access_log=True)

        try:
            import os
            fd = os.open("/dev/tty", os.O_WRONLY)
            os.write(fd, b"\033[H\033[2J\033[3J")
            os.close(fd)
        except OSError:
            pass

        if ngrok:
            try:
                from pyngrok import ngrok as _ngrok
                _ngrok.kill()
            except Exception:
                pass


def _wait_for_first_participant(base_url: str) -> None:
    import requests
    from rich.console import Console
    console = Console()
    with console.status("[cyan]Waiting for first participant to connect...[/]", spinner="dots"):
        while True:
            try:
                r = requests.get(f"{base_url}/health", timeout=1)
                if r.json().get("users", 0) >= 1:
                    break
            except Exception:
                pass
            time.sleep(0.5)


def _print_ngrok_panel(ngrok_addr: str, password: str) -> None:
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    connect_cmd = f"python cmd_chat.py connect {ngrok_addr} 443 <username> <password>"
    console.print(Panel(
        f"[bold green]ngrok tunnel active (HTTPS)[/]\n\n"
        f"[cyan]Address:[/] https://{ngrok_addr}:443\n\n"
        f"[cyan]Connect:[/] {connect_cmd}\n\n"
        f"[bold red]⚠ The shared password must be pre-known and never sent over this channel.[/]\n"
        f"[red]Anyone with the password can join and decrypt all messages.[/]\n"
        f"[red]Sharing it here destroys all security guarantees.[/]",
        title="[bold]Public Access[/]",
        expand=False,
    ))


def _run_with_client(
    host: str,
    port: int,
    password: str,
    username: str,
    ngrok: bool = False,
    ngrok_token: Optional[str] = None,
) -> None:
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

    ngrok_addr: Optional[str] = None
    if ngrok:
        ngrok_addr = _start_ngrok(port, ngrok_token)
        if ngrok_addr:
            _print_ngrok_panel(ngrok_addr, password)
            _wait_for_first_participant(base_url)

    def _kill_server():
        proc.terminate()
        proc.join(timeout=1)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1)
        if ngrok:
            try:
                from pyngrok import ngrok as _ngrok
                _ngrok.kill()
            except Exception:
                pass

    Client(
        server=client_host,
        port=port,
        username=username,
        password=password,
        is_host=True,
        ngrok_addr=ngrok_addr,
        on_disconnect=_kill_server,
    ).run()

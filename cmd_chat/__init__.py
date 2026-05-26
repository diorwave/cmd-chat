import argparse
import getpass
import os

from cmd_chat.server.server import run_server
from cmd_chat.client.client import Client


def resolve_password(args_password: str | None, prompt: str = "Room password: ") -> str:
    if args_password:
        return args_password
    if env_pw := os.environ.get("CMD_CHAT_PASSWORD"):
        return env_pw
    return getpass.getpass(prompt)


def main():
    parser = argparse.ArgumentParser(description="Command-line chat application")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_p = subparsers.add_parser("serve", help="Run server")
    serve_p.add_argument("ip_address")
    serve_p.add_argument("port")
    serve_p.add_argument("--password", "-p", default=None)
    serve_p.add_argument("--cert", default=None, help="Path to TLS certificate")
    serve_p.add_argument("--key", default=None, help="Path to TLS private key")
    serve_p.add_argument("--no-tls", action="store_true", help="Disable TLS (insecure)")

    connect_p = subparsers.add_parser("connect", help="Connect to server")
    connect_p.add_argument("ip_address")
    connect_p.add_argument("port")
    connect_p.add_argument("username")
    connect_p.add_argument("--password", "-p", default=None)
    connect_p.add_argument(
        "--insecure", "-k", action="store_true",
        help="Skip TLS certificate verification (for self-signed certs)",
    )
    connect_p.add_argument(
        "--no-tls", action="store_true",
        help="Connect without TLS (insecure)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        password = resolve_password(args.password)
        run_server(
            host=args.ip_address,
            port=int(args.port),
            password=password,
            cert_path=args.cert,
            key_path=args.key,
            no_tls=args.no_tls,
        )
    elif args.command == "connect":
        password = resolve_password(args.password)
        Client(
            server=args.ip_address,
            port=int(args.port),
            username=args.username,
            password=password,
            insecure=args.insecure,
            no_tls=args.no_tls,
        ).run()


if __name__ == "__main__":
    main()

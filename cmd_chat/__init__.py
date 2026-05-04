import argparse
import getpass


def _prompt(label: str, secret: bool = False) -> str:
    while True:
        value = (getpass.getpass(label) if secret else input(label)).strip()
        if value:
            return value


def main():
    parser = argparse.ArgumentParser(description="Command-line chat application")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_p = subparsers.add_parser("serve", help="Run server")
    serve_p.add_argument("ip_address")
    serve_p.add_argument("port")
    serve_p.add_argument("--password", "-p", default=None)
    serve_p.add_argument("--join", "-j", nargs="?", const=True, default=False, metavar="USERNAME",
                         help="Join chat as host (prompts for username if not given)")
    serve_p.add_argument("--ngrok", action="store_true", help="Expose server via ngrok tunnel")
    serve_p.add_argument("--ngrok-token", metavar="TOKEN", help="ngrok authtoken")

    connect_p = subparsers.add_parser("connect", help="Connect to server")
    connect_p.add_argument("ip_address")
    connect_p.add_argument("port")
    connect_p.add_argument("username", nargs="?", default=None)
    connect_p.add_argument("password", nargs="?", default=None)

    args = parser.parse_args()

    if args.command == "serve":
        password = args.password or _prompt("Password: ", secret=True)

        if args.join is True:
            join_as = _prompt("Your username: ")
        elif args.join:
            join_as = args.join
        else:
            join_as = None

        from cmd_chat.server.server import run_server
        run_server(
            host=args.ip_address,
            port=int(args.port),
            password=password,
            join_as=join_as,
            ngrok=args.ngrok,
            ngrok_token=args.ngrok_token,
        )

    elif args.command == "connect":
        username = args.username or _prompt("Username: ")
        password = args.password or _prompt("Password: ", secret=True)

        from cmd_chat.client.client import Client
        Client(
            server=args.ip_address,
            port=int(args.port),
            username=username,
            password=password,
        ).run()


if __name__ == "__main__":
    main()

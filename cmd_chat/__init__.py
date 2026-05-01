import argparse


def main():
    parser = argparse.ArgumentParser(description="Command-line chat application")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_p = subparsers.add_parser("serve", help="Run server")
    serve_p.add_argument("ip_address")
    serve_p.add_argument("port")
    serve_p.add_argument("--password", "-p", required=True)
    serve_p.add_argument("--join", "-j", metavar="USERNAME", help="Join chat as this user after server starts")
    serve_p.add_argument("--ngrok", action="store_true", help="Expose server via ngrok tunnel")
    serve_p.add_argument("--ngrok-token", metavar="TOKEN", help="ngrok authtoken")

    connect_p = subparsers.add_parser("connect", help="Connect to server")
    connect_p.add_argument("ip_address")
    connect_p.add_argument("port")
    connect_p.add_argument("username")
    connect_p.add_argument("password")

    args = parser.parse_args()

    if args.command == "serve":
        from cmd_chat.server.server import run_server
        run_server(
            host=args.ip_address,
            port=int(args.port),
            password=args.password,
            join_as=args.join,
            ngrok=args.ngrok,
            ngrok_token=args.ngrok_token,
        )
    elif args.command == "connect":
        from cmd_chat.client.client import Client
        Client(
            server=args.ip_address,
            port=int(args.port),
            username=args.username,
            password=args.password,
        ).run()


if __name__ == "__main__":
    main()

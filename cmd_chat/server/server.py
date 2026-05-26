import ipaddress
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .factory import create_app

DEFAULT_CERT_DIR = Path.home() / ".cmd-chat" / "certs"


def ensure_tls_certs(cert_dir: Path = DEFAULT_CERT_DIR) -> tuple[Path, Path]:
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "server.pem"
    key_path = cert_dir / "server-key.pem"

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "cmd-chat"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "cmd-chat-self-signed"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return cert_path, key_path


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    password: Optional[str] = None,
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    no_tls: bool = False,
) -> None:
    app = create_app(password=password or "")

    ssl_ctx = None
    if not no_tls:
        if cert_path and key_path:
            c, k = Path(cert_path), Path(key_path)
        else:
            c, k = ensure_tls_certs()
            print(f"[TLS] Auto-generated self-signed cert: {c}")

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(str(c), str(k))
        protocol = "https"
    else:
        print("[WARNING] TLS disabled — all traffic is unencrypted!")
        protocol = "http"

    print(f"[ADMIN] Clear token: {app.ctx.admin_token}")
    print(f"[SERVER] Listening on {protocol}://{host}:{port}")

    app.run(
        host=host,
        port=port,
        single_process=True,
        debug=False,
        access_log=False,
        ssl=ssl_ctx,
    )

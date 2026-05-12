"""
Self-signed TLS certificate auto-generator.

Browsers block plain-HTTP requests from HTTPS pages (mixed content). Since
many sites we want to capture from (openrouter.ai, github.com, claude.ai)
are HTTPS, the capman server needs to accept HTTPS too — even with a
self-signed cert. The user adds a one-time browser exception, then
everything works.

Cert is generated once on first start and cached in {data_dir}/tls/.
"""
from __future__ import annotations

import datetime
import ipaddress
import logging
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_tls_cert(data_dir: Path, host: str) -> tuple[Path, Path]:
    """
    Generate a self-signed cert valid for localhost + the daemon's host IP +
    any locally-detected IPs. Returns (cert_path, key_path).
    """
    tls_dir = Path(data_dir).expanduser() / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tls_dir / "capman.crt"
    key_path = tls_dir / "capman.key"

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        logger.warning(
            "cryptography package not installed — TLS disabled. "
            "Install with: uv add cryptography"
        )
        return None, None

    # Build SAN list with all useful hostnames + IPs
    san_entries: list = [x509.DNSName("localhost")]
    san_entries.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    san_entries.append(x509.IPAddress(ipaddress.IPv6Address("::1")))

    # Add the configured listening host if it's an IP
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        if host and host != "0.0.0.0":
            san_entries.append(x509.DNSName(host))

    # Add hostname
    try:
        hostname = socket.gethostname()
        san_entries.append(x509.DNSName(hostname))
    except Exception:
        pass

    # Add all local IPv4 addresses on every network interface
    # (getaddrinfo only returns the loopback on most systems)
    detected_ips: set[str] = set()
    try:
        # Method 1: ip command (Linux)
        import subprocess
        result = subprocess.run(
            ["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=2
        )
        for line in result.stdout.splitlines():
            for token in line.split():
                if "/" in token:
                    ip = token.split("/")[0]
                    try:
                        ipaddress.IPv4Address(ip)
                        detected_ips.add(ip)
                    except ValueError:
                        pass
    except Exception:
        pass

    # Method 2: socket trick — detect outbound IP for default route
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        detected_ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    for ip in detected_ips:
        try:
            san_entries.append(x509.IPAddress(ipaddress.IPv4Address(ip)))
        except ValueError:
            pass

    # Deduplicate
    seen = set()
    san_unique = []
    for s in san_entries:
        key = (type(s).__name__, str(s.value))
        if key not in seen:
            seen.add(key)
            san_unique.append(s)

    logger.info("Generating self-signed TLS cert with SAN: %s",
                ", ".join(str(s.value) for s in san_unique))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "capman2 local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "capman2"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_unique), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o644)
    key_path.chmod(0o600)
    logger.info("TLS cert generated at %s", cert_path)
    return cert_path, key_path

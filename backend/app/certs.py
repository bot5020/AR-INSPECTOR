"""Локальный удостоверяющий центр и сертификат сервера для HTTPS в локальной сети.

Камера в браузере и установка PWA требуют HTTPS. Интернета на объекте нет, поэтому
публичные сертификаты недоступны: создаём свой УЦ, его сертификат один раз ставится
на планшет/телефон (Настройки → Безопасность → Установить сертификат → Сертификат ЦС).
"""
from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import config

CERT_DIR = config.DATA_DIR / "certs"
CA_CERT = CERT_DIR / "ca.crt"
CA_KEY = CERT_DIR / "ca.key"
SRV_CERT = CERT_DIR / "server.crt"
SRV_KEY = CERT_DIR / "server.key"
SRV_HOSTS = CERT_DIR / "server.hosts"


def local_ips() -> list[str]:
    ips: set[str] = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    # Адрес интерфейса по умолчанию (без реальной отправки пакетов).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("169.254."))


def primary_ip(ips: list[str] | None = None) -> str | None:
    """Адрес для телефона: сначала домашние/офисные сети 192.168.x, затем 10.x, затем 172.16–31.x.

    Маршрут по умолчанию не подходит: VPN-адаптеры часто перехватывают его.
    """
    def rank(ip: str) -> int:
        a, b = (int(x) for x in ip.split(".")[:2])
        if a == 192 and b == 168:
            return 0
        if a == 10:
            return 1
        if a == 172 and 16 <= b <= 31:
            return 2
        return 3
    cands = [ip for ip in (ips or local_ips()) if ip != "127.0.0.1" and ip[0].isdigit()]
    return min(cands, key=rank) if cands else None


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _ensure_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    if CA_CERT.exists() and CA_KEY.exists():
        return (x509.load_pem_x509_certificate(CA_CERT.read_bytes()),
                serialization.load_pem_private_key(CA_KEY.read_bytes(), None))  # type: ignore[return-value]
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AR-Инспектор локальный УЦ"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AR-Инспектор (прототип)")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    CA_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_key(CA_KEY, key)
    return cert, key


def ensure_server_cert(extra_hosts: list[str] | None = None) -> tuple[Path, Path, list[str]]:
    """Перевыпускает серверный сертификат, если сменились IP-адреса ноутбука."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = _ensure_ca()
    hosts = sorted(set(["localhost", socket.gethostname().lower()] + local_ips() + (extra_hosts or [])))
    if SRV_CERT.exists() and SRV_KEY.exists() and SRV_HOSTS.exists() and SRV_HOSTS.read_text() == ",".join(hosts):
        return SRV_CERT, SRV_KEY, hosts
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for h in hosts:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AR-Инспектор")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    SRV_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + CA_CERT.read_bytes())
    _write_key(SRV_KEY, key)
    SRV_HOSTS.write_text(",".join(hosts))
    return SRV_CERT, SRV_KEY, hosts


def ca_der() -> bytes:
    return x509.load_pem_x509_certificate(CA_CERT.read_bytes()).public_bytes(serialization.Encoding.DER)

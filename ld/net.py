"""Определение локальных адресов — по ним телефоны заходят в игру."""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess

# Интерфейсы, которые почти наверняка не ведут к телефонам в комнате.
_SKIP_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "zt", "podman")


def _is_useful(ip: str) -> bool:
    """Годится ли адрес, чтобы дать его друзьям.

    Проверять на «частный» нельзя: Radmin VPN и подобные раздают адреса из
    26.0.0.0/8, Hamachi — из 25.0.0.0/8, и формально они не частные. Поэтому
    отбрасываем только заведомо бесполезное.
    """
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_unspecified or addr.is_reserved)


def default_route_ip() -> str | None:
    """Адрес интерфейса, через который система пошла бы «наружу».

    Работает и без интернета: connect на UDP-сокете лишь выбирает маршрут
    и не отправляет ни одного пакета.
    """
    for probe in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.2)
            sock.connect((probe, 9))
            ip = sock.getsockname()[0]
            if _is_useful(ip):
                return ip
        except OSError:
            continue
        finally:
            sock.close()
    return None


def _from_ip_command() -> list[tuple[str, str]]:
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        iface, cidr = parts[1], parts[3]
        ip = cidr.split("/")[0]
        if iface.startswith(_SKIP_PREFIXES) or not _is_useful(ip):
            continue
        found.append((iface, ip))
    return found


def _from_hostname() -> list[tuple[str, str]]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return [("?", i[4][0]) for i in infos if _is_useful(i[4][0])]


def addresses() -> list[tuple[str, str]]:
    """Все правдоподобные локальные адреса; самый вероятный — первым."""
    found = _from_ip_command() or _from_hostname()
    primary = default_route_ip()

    seen, ordered = set(), []
    if primary:
        ordered.append((next((i for i, a in found if a == primary), "?"), primary))
        seen.add(primary)
    for iface, ip in found:
        if ip not in seen:
            ordered.append((iface, ip))
            seen.add(ip)
    return ordered


def best_address() -> str:
    found = addresses()
    return found[0][1] if found else "127.0.0.1"


def join_url(port: int, host: str | None = None) -> str:
    """Адрес, который показываем друзьям.

    Хост может прийти из настроек как есть — из туннеля или виртуальной сети,
    и тогда он уже содержит свой порт. Свой порт в таком случае не приписываем.
    """
    host = (host or "").strip() or best_address()
    # Целую ссылку (её выдают туннели) берём как есть: там свой протокол
    # и обычно обычный 443-й порт, приписывать наш не нужно.
    if re.match(r"^https?://", host, flags=re.I):
        return host.rstrip("/") + "/"
    host = host.rstrip("/")
    tail = host.rsplit(":", 1)[-1]
    if ":" in host and tail.isdigit() and "]" not in tail:
        return f"http://{host}/"
    return f"http://{host}:{port}/"


def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_port(preferred: int) -> int:
    """Возвращает свободный порт, начиная с желаемого."""
    for candidate in range(preferred, preferred + 20):
        if port_is_free(candidate):
            return candidate
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

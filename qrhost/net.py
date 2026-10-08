"""Find the addresses other devices on the network can reach this machine at."""

import ipaddress
import socket


def _usable(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


def lan_ips():
    """Return LAN IPv4 addresses, the most likely one first (falls back to loopback)."""
    found = []

    # The address the OS would use to route outward is almost always the right one.
    # Connecting a UDP socket sends no packets.
    for probe in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                found.append(s.getsockname()[0])
        except OSError:
            pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass

    ips = []
    for ip in found:
        if _usable(ip) and ip not in ips:
            ips.append(ip)
    # Prefer private (home/office) networks over e.g. VPN or public addresses.
    ips.sort(key=lambda ip: not ipaddress.ip_address(ip).is_private)
    return ips or ["127.0.0.1"]

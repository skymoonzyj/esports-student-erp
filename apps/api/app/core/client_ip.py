from __future__ import annotations

import ipaddress

from starlette.requests import Request

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def trusted_proxy_networks(value: str) -> tuple[IPNetwork, ...]:
    if not value.strip():
        return ()

    networks: list[IPNetwork] = []
    for raw_cidr in value.split(","):
        cidr = raw_cidr.strip()
        if not cidr:
            raise ValueError("trusted proxy CIDR entries must not be empty")
        networks.append(ipaddress.ip_network(cidr))
    return tuple(networks)


def _normalized_ip(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def request_source_ip(request: Request, *, trusted_proxy_cidrs: str) -> str:
    if request.client is None:
        return "unknown"

    peer_host = request.client.host.strip()
    peer_ip = _normalized_ip(peer_host)
    socket_source = peer_ip.compressed if peer_ip is not None else peer_host or "unknown"

    try:
        networks = trusted_proxy_networks(trusted_proxy_cidrs)
    except ValueError:
        return socket_source
    if peer_ip is None or not any(peer_ip in network for network in networks):
        return socket_source

    # Proxies append their immediate peer. Walk from the trusted socket toward
    # the client and stop at the first untrusted address; anything before it
    # can have been supplied by that client and must not influence attribution.
    chain_values = request.headers.getlist("x-forwarded-for")
    if chain_values:
        if len(chain_values) != 1 or len(chain_values[0]) > 2048:
            return socket_source
        parts = chain_values[0].split(",")
        if len(parts) > 32:
            return socket_source
        addresses = [_normalized_ip(part) for part in parts]
        if any(address is None for address in addresses):
            return socket_source
        for address in reversed(addresses):
            assert address is not None
            if not any(address in network for network in networks):
                return address.compressed
        # A chain made entirely of trusted infrastructure has no verified
        # external source. Keep its socket attribution rather than guessing.
        return socket_source

    forwarded_values = request.headers.getlist("x-real-ip")
    if len(forwarded_values) != 1:
        return socket_source
    forwarded = forwarded_values[0].strip()
    if not forwarded or "," in forwarded:
        return socket_source
    forwarded_ip = _normalized_ip(forwarded)
    return forwarded_ip.compressed if forwarded_ip is not None else socket_source

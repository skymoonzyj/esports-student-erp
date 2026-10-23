from __future__ import annotations

import socket
import sys


class E2EPortUnavailable(RuntimeError):
    pass


def require_local_port_free(port: int) -> None:
    if not 1 <= port <= 65535:
        raise E2EPortUnavailable
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise E2EPortUnavailable from None


def main() -> int:
    if len(sys.argv) != 2:
        print("本地 E2E 端口配置无效", file=sys.stderr)
        return 2
    try:
        port = int(sys.argv[1])
        require_local_port_free(port)
    except (E2EPortUnavailable, ValueError):
        print("本地端口已被其他进程占用或不可用", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

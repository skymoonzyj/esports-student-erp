"""Fixed-origin, streaming WebDAV without redirects or ambient proxy support."""

import base64
import http.client
import socket
import threading
import time
from urllib.parse import quote, urlsplit


class CloudError(RuntimeError):
    pass


def safe_path(path):
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or len(path) > 1024
        or any(c in path for c in "%?#\\\r\n")
        or any(p in ("", ".", "..") for p in path[1:].split("/"))
        or any(ord(c) < 32 for c in path)
    ):
        raise CloudError("cloud_invalid_path")
    return path


def target_prefix(path):
    """An internally scoped WebDAV root has an empty prefix, not a double slash."""
    return "" if path == "/" else safe_path(path)


class Transport:
    def __init__(
        self,
        origin,
        username,
        password,
        progress=None,
        *,
        socket_timeout=30,
        heartbeat_interval=60,
        managed_auth=False,
    ):
        parts = urlsplit(origin)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise CloudError("cloud_connector_failed")
        self.parts = parts
        self.authorization = (
            "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
        )
        self.deadline = time.monotonic() + 6 * 60 * 60
        self.progress = progress
        self.socket_timeout = socket_timeout
        self.heartbeat_interval = heartbeat_interval
        self.managed_auth = managed_auth

    def wait_response(self, connection):
        """Connector PUT may finish its provider upload after consuming the body."""
        self.check_deadline()
        connection.sock.settimeout(max(0.001, self.deadline - time.monotonic()))
        stopped = threading.Event()
        errors = []
        active_socket = connection.sock

        def refresh():
            while not stopped.wait(self.heartbeat_interval):
                try:
                    self.check_deadline()
                except Exception:  # noqa: BLE001 -- thread boundary: never leak callback details
                    errors.append(True)
                    try:
                        active_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

        worker = threading.Thread(
            target=refresh, daemon=True, name="erp-cloud-heartbeat"
        )
        worker.start()
        try:
            response = connection.getresponse()
            if errors:
                raise CloudError("cloud_connector_failed")
            active_socket.settimeout(self.socket_timeout)
            return response
        finally:
            stopped.set()
            # Bridge calls have their own <=60s timeout; daemon cannot block process exit.
            worker.join(timeout=60)

    def request(
        self, method, path, source=None, destination=None, limit=65536, missing=False
    ):
        path = "/dav" + quote(safe_path(path), safe="/")
        connection_class = (
            http.client.HTTPSConnection
            if self.parts.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            self.parts.hostname, self.parts.port, timeout=self.socket_timeout
        )
        try:
            connection.putrequest(method, path)
            connection.putheader("Authorization", self.authorization)
            connection.putheader(
                "Content-Length", str(source.stat().st_size if source else 0)
            )
            connection.endheaders()
            if source:
                with source.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        self.check_deadline()
                        connection.send(chunk)
            response = self.wait_response(connection)
            if 300 <= response.status < 400:
                raise CloudError("cloud_redirect_rejected")
            if response.status == 401 and self.managed_auth:
                raise CloudError("cloud_authorization_invalid")
            if missing and response.status == 404:
                return False
            if not 200 <= response.status < 300 and not (
                method == "MKCOL" and response.status == 405
            ):
                raise CloudError("cloud_connector_failed")
            if destination:
                size = 0
                with destination.open("xb") as stream:
                    import os

                    os.chmod(destination, 0o600)
                    while chunk := response.read(min(1024 * 1024, limit - size + 1)):
                        self.check_deadline()
                        size += len(chunk)
                        if size > limit:
                            raise CloudError("cloud_remote_corrupt")
                        stream.write(chunk)
            else:
                if len(response.read(65537)) > 65536:
                    raise CloudError("cloud_connector_failed")
            return True
        except CloudError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise CloudError("cloud_connector_failed") from None
        finally:
            connection.close()

    def check_deadline(self):
        if time.monotonic() > self.deadline:
            raise CloudError("cloud_connector_failed")
        if self.progress:
            self.progress()

    def get(self, path, destination, limit, missing=False):
        return self.request(
            "GET", path, destination=destination, limit=limit, missing=missing
        )

    def put(self, path, source):
        self.request("PUT", path, source=source)

    def delete(self, path):
        self.request("DELETE", path)

    def mkdir(self, path):
        self.request("MKCOL", path)

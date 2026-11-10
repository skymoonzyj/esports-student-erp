"""Physical/OS/HTTPS boundary adapters; all application behavior stays real."""

import hashlib
import hmac
import io
import json
import os
import ssl
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient

SOURCE = os.environ.get("RX_CONNECTOR_SOURCE")
if not SOURCE or not (Path(SOURCE) / "src/connector_service.py").is_file():
    raise RuntimeError("RX_CONNECTOR_SOURCE must explicitly name the actual connector checkout")
sys.path.insert(0, str(Path(SOURCE).resolve() / "src"))

from connector_api import ConnectorApi
from connector_service import ConnectorService
from connector_store import ConnectorStore
from connector_windows import Credential
from device_runtime import DeviceRuntime
from training import Engine

from tests.api.conftest import _login_as
from tests.api.test_reactionx_pairing import ORIGIN, WEB
from tests.api.test_reactionx_runs import spec

FORMAL_MODES = (
    "standard",
    "sequence",
    "simultaneous",
    "base",
    "auditory",
    "true_false",
    "counter",
    "whack",
)


class TempProtection:
    """Authenticated test cipher, explicitly not DPAPI or Windows acceptance."""

    def protect(self, raw):
        payload = bytes(c ^ 0xAA for c in raw)
        return hmac.digest(b"synthetic-cross-service-key", payload, "sha256") + payload

    def unprotect(self, blob):
        if not hmac.compare_digest(
            blob[:32], hmac.digest(b"synthetic-cross-service-key", blob[32:], "sha256")
        ):
            raise RuntimeError("synthetic protection integrity failure")
        return bytes(c ^ 0xAA for c in blob[32:])


class TempDirectory:
    def __init__(self, path):
        self.path = Path(path)

    def ensure(self):
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def check_file(self, path):
        if Path(path).is_symlink() or Path(path).parent != self.path:
            raise RuntimeError("synthetic directory boundary failure")


class Gateway:
    """No dispatch/repair: forward original method, URL, headers and raw bytes.

    One lock serializes browser requests, machine requests and direct shared
    PostgreSQL Session access. Faults occur only AFTER the real HTTP response.
    """

    def __init__(self, browser):
        self.browser = browser
        self.machine = TestClient(browser.app, base_url=ORIGIN, follow_redirects=False)
        self.lock = threading.RLock()
        self.changed = threading.Condition()
        self.machine_cookie_count = 0
        self.tls_checked = False
        self.result_bodies = []
        self.result_outcomes = []
        self.drop_result_response = False
        self.dropped_result = None
        self.hold_start_response = False
        self.start_delivered = threading.Event()
        self.release_start = threading.Event()
        self.declaration_bodies = []
        self.hold_archive_response = False
        self.archive_delivered = threading.Event()
        self.release_archive = threading.Event()
        self.dropped_archive_receipt = None
        self.lose_terminal_ack = False
        self.terminal_ack_lost = threading.Event()
        self.offline = False

    def notify(self):
        with self.changed:
            self.changed.notify_all()

    def factory(self, host, *, port, timeout, context):
        expected = urlsplit(ORIGIN)
        assert host == expected.hostname and port == expected.port
        assert timeout == 5
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
        self.tls_checked = True
        return Connection(self)

    def forward(self, method, path, body, headers):
        with self.lock:
            if self.offline:
                raise OSError("synthetic network unavailable")
            if (
                self.lose_terminal_ack
                and method == "POST"
                and path.endswith("/ack")
                and json.loads(body)["run_state"] == "stopped_confirmed"
            ):
                self.lose_terminal_ack = False
                self.offline = True
                self.terminal_ack_lost.set()
                self.notify()
                raise OSError("synthetic network loss before terminal ACK reached ERP")
            assert not self.machine.cookies
            assert not any(k.lower() == "cookie" for k in headers)
            response = self.machine.request(method, path, content=body, headers=headers)
            self.machine_cookie_count += int("cookie" in response.request.headers)
            assert not self.machine.cookies
            if method == "POST" and path.endswith("/result"):
                self.result_bodies.append(body)
                self.result_outcomes.append((response.status_code, response.json().get("code")))
                if self.drop_result_response and response.status_code == 200:
                    self.drop_result_response = False
                    self.dropped_result = response.json()
                    self.notify()
                    raise OSError("synthetic response loss after real result commit")
            if method == "POST" and path.endswith("/uncertainty"):
                self.declaration_bodies.append(body)
            hold_archive = (
                self.hold_archive_response
                and method == "GET"
                and "/uncertainty?" in path
                and response.status_code == 200
                and response.json()["state"] == "archived"
            )
            hold = (
                self.hold_start_response
                and method == "GET"
                and "/commands?" in path
                and response.status_code == 200
                and any(c["kind"] == "start" for c in response.json()["commands"])
            )
        self.notify()
        if hold_archive:
            self.hold_archive_response = False
            self.dropped_archive_receipt = response.json()["receipt"]
            self.archive_delivered.set()
            if not self.release_archive.wait(4):
                raise OSError("bounded archive response-loss boundary expired")
            raise OSError("synthetic process loss before archive receipt consumption")
        if hold:
            self.hold_start_response = False
            self.start_delivered.set()
            if not self.release_start.wait(4):
                raise OSError("bounded crash boundary expired")
            raise OSError("synthetic process loss after server delivery, before local consumption")
        return response


class Connection:
    sock = None

    def __init__(self, gateway):
        self.gateway = gateway

    def request(self, method, path, *, body, headers):
        self.request_args = method, path, body, headers

    def getresponse(self):
        result = self.gateway.forward(*self.request_args)
        response = io.BytesIO(result.content)
        response.status = result.status_code
        return response

    def close(self):
        pass


class Physical:
    """Only device transport: real runtime interprets notifications and writes."""

    def __init__(self, receive, notify):
        self.receive, self.notify = receive, notify
        self.writes, self.bindings = [], {}
        self.fail_off = False
        self.off_failed = threading.Event()
        self.lamps = {
            d["device_key"]: SimpleNamespace(
                name="$react-synthetic",
                number=d["display_number"],
                version=d["protocol_version"],
                device_key=d["device_key"],
                client=SimpleNamespace(is_connected=True),
            )
            for d in spec()["devices"]
        }

    @property
    def enable_writes(self):
        return [(key, packet) for key, packet in self.writes if packet != b"\x05"]

    def identity_bindings(self):
        return {"synthetic-private-" + key: key for key in self.lamps} | self.bindings

    def restore_identity_bindings(self, bindings):
        self.bindings.update(bindings)

    async def write(self, key, packet):
        assert key in self.lamps
        if packet == b"\x05" and self.fail_off:
            self.off_failed.set()
            raise OSError("synthetic OFF transport failure")
        self.writes.append((key, packet))
        self.notify()

    async def connect(self, keys):
        for key in keys:
            self.lamps[key].client.is_connected = True

    async def scan(self, duration=2):
        return []

    async def disconnect(self):
        for lamp in self.lamps.values():
            lamp.client.is_connected = False


class Harness:
    def __init__(self, browser, db, login, users, path):
        self.browser, self.db, self.auth, self.users, self.path = browser, db, login, users, path
        self.person_id = str(users["accounts"]["admin"].person_id)
        self.gateway = Gateway(browser)
        self.tab = str(uuid4())
        response = self.request("POST", WEB + "/pairings", json={"browser_tab_id": self.tab})
        assert response.status_code == 200, response.text
        challenge = response.json()
        api = ConnectorApi(ORIGIN, connection_factory=self.gateway.factory)
        secret = "A" * 43  # canonical synthetic test credential only; never logged
        claimed = api.claim(
            challenge["challenge"], hashlib.sha256(secret.encode()).hexdigest(), "synthetic-c6"
        )
        self.credential = Credential(
            ORIGIN, claimed["actor_account_id"], claimed["connector_id"], secret
        )
        api.credentials = self.credential
        body = {"verification_phrase": claimed["verification_phrase"]}
        response = self.request(
            "POST", WEB + f"/pairings/{claimed['pairing_id']}/confirm", json=body
        )
        assert response.status_code == 200, response.text
        assert (
            api.confirm(claimed["pairing_id"], claimed["verification_phrase"])["status"] == "active"
        )
        api.close()
        self.assemble()

    def assemble(self):
        self.store = ConnectorStore(
            origin=ORIGIN,
            actor_id=self.credential.actor_id,
            connector_id=self.credential.connector_id,
            private_directory=TempDirectory(self.path),
            protector=TempProtection(),
            wait_seconds=2,
        )
        self.runtime = DeviceRuntime(
            lambda *_: None,
            transport_factory=lambda receive: Physical(receive, self.gateway.notify),
            engine_factory=Engine,
        )
        self.physical = self.runtime.transport
        self.api = ConnectorApi(ORIGIN, self.credential, connection_factory=self.gateway.factory)
        self.service = ConnectorService(
            self.runtime, self.store, self.api, self.credential, device_timeout=0.2
        )
        original_emit = self.runtime.emit

        def emit(kind, value):
            original_emit(kind, value)
            self.gateway.notify()

        self.runtime.emit = emit

    def request(self, method, path, **kwargs):
        with self.gateway.lock:
            response = self.browser.request(
                method, path, headers={"X-CSRF-Token": self.auth.csrf_token}, **kwargs
            )
        self.gateway.notify()
        return response

    def wait(self, predicate, description, timeout=7):
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return
            left = deadline - time.monotonic()
            assert left > 0, f"{description}; connector={self.service.status()}"
            # Real IO/runtime events wake observers; bounded fallback catches
            # status publication immediately following a response callback.
            with self.gateway.changed:
                self.gateway.changed.wait(min(left, 0.05))

    def boot(self):
        self.service.run()
        self.wait(lambda: self.service.status()["online"], "actual heartbeat and poll")

    def prepare(self, mode="manual"):
        return self.request(
            "POST",
            WEB + "/runs/prepare",
            json={
                "operation_id": str(uuid4()),
                "person_id": self.person_id,
                "connector_id": self.credential.connector_id,
                "tab_id": self.tab,
                **spec(mode),
            },
        )

    def prepare_ready(self, mode="manual"):
        response = self.prepare(mode)
        assert response.status_code == 202, response.text
        run = response.json()
        self.wait(lambda: self.run_view(run)["status"] == "ready", "real prepare ACK")
        return run

    def run_view(self, run):
        response = self.request("GET", WEB + f"/runs/{run['run_id']}")
        assert response.status_code == 200, response.text
        return response.json()

    def command(self, run, kind, **extra):
        response = self.request(
            "POST",
            WEB + f"/runs/{run['run_id']}/{kind}",
            json={
                "operation_id": str(uuid4()),
                "tab_id": self.tab,
                **extra,
            },
        )
        assert response.status_code == 202, response.text
        return response.json()

    def start(self, run):
        self.command(run, "start")
        self.wait(
            lambda: self.runtime.engine.running and self.runtime.engine.ready_for_targets,
            "real Engine start",
        )

    def manual_hit(self, run):
        self.command(run, "manual-activate", payload={"device_key": "lamp-a", "color": 2})
        self.wait(
            lambda: any(
                e["kind"] == "activated" and e["device_key"] == "lamp-a"
                for e in self.store.get_run(run["run_id"]).events
            ),
            "durably recorded completed physical activation",
        )
        self.service._loop.call_soon_threadsafe(
            self.physical.receive,
            "notification",
            {
                "kind": "hit",
                "key": "lamp-a",
                "device_ticks_10ms": 0,
            },
        )
        self.wait(
            lambda: any(e["kind"] == "hit" for e in self.store.get_run(run["run_id"]).events),
            "durable actual Engine hit",
        )

    def logout(self):
        response = self.request("POST", "/api/v1/auth/logout")
        assert response.status_code == 204, response.text

    def login(self, key="admin"):
        with self.gateway.lock:
            self.auth = _login_as(self.browser, self.users, key)

    def unknown_run(self, *, fail_off=False):
        """The original real server delivery / absent local START-accept window."""
        self.boot()
        run = self.prepare_ready()
        self.gateway.hold_start_response = True
        self.command(run, "start")
        assert self.gateway.start_delivered.wait(4), "real server never delivered start"
        assert self.store.get_run(run["run_id"]).status == "prepared"
        assert not self.physical.enable_writes
        self.crash_and_reopen()
        self.physical.fail_off = fail_off
        self.command(run, "stop")
        if fail_off:
            self.service.run()
            assert self.physical.off_failed.wait(4), "actual OFF failure boundary was not reached"
        else:
            self.boot()
            self.wait(lambda: self.run_view(run)["stop_confirmed"], "recovery exact OFF ACK")
        return run

    def declared_uncertainty(self, run):
        """Wait for actual D2 protected evidence to reach the actual D1 endpoint."""

        def declared():
            response = self.request("GET", WEB + "/uncertainties")
            assert response.status_code == 200, response.text
            return any(item["run_id"] == run["run_id"] for item in response.json()["items"])

        self.wait(declared, "actual original-credential uncertainty declaration")
        declarations = self.store.pending_uncertainties()
        assert len(declarations) == 1 and declarations[0]["run_id"] == run["run_id"]
        return declarations[0]

    def archive_request(self, run, digest):
        response = self.request(
            "POST",
            WEB + f"/runs/{run['run_id']}/uncertainty/request",
            json={
                "declaration_digest": digest,
                "reason": "Synthetic original STOP and physical OFF checked; execution remains unknown",
                "confirmation": True,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def stop_workers(self):
        self.physical.fail_off = False
        self.gateway.release_start.set()
        self.gateway.release_archive.set()
        self.service.close()
        for worker in (
            self.service._thread,
            self.service._network_thread,
            self.service._watchdog_thread,
        ):
            if worker:
                worker.join(3)
                assert not worker.is_alive(), f"leaked {worker.name}"
        assert not self.store._thread.is_alive(), "leaked protected store worker"

    def crash_and_reopen(self):
        # Non-running crash: stop the owners without graceful stop/finalize.
        # This is process-loss simulation, not native OS process-kill acceptance.
        assert not self.runtime.engine.running
        self.service._terminated.set()
        self.service._wake.set()
        self.service._loop.call_soon_threadsafe(self.service._loop.stop)
        self.gateway.release_start.set()
        if self.gateway.archive_delivered.is_set():
            self.gateway.release_archive.set()
        for worker in (
            self.service._thread,
            self.service._network_thread,
            self.service._watchdog_thread,
        ):
            worker.join(3)
            assert not worker.is_alive()
        assert not self.store._thread.is_alive()
        self.assemble()

    def close(self):
        try:
            self.stop_workers()
        finally:
            self.gateway.machine.close()

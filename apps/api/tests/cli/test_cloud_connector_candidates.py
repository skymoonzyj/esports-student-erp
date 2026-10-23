"""Host connector contracts, with external HTTP replaced by a stateful double."""

import base64
import hashlib
import http.client
import importlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def modules():
    if not (ROOT / "scripts/cloud_auth/openlist_admin.py").exists():
        pytest.fail("ERP-owned candidate mounting is not implemented")
    return importlib.import_module("cloud_auth.openlist_admin"), importlib.import_module(
        "cloud_auth.candidate"
    )


class Journal:
    def __init__(self):
        self.records = {}
        self.probes = []
        self.finished = []

    def reserve(self, record):
        return self.records.setdefault(record.operation_id, record)

    def read(self, operation_id):
        return self.records.get(operation_id)

    def save(self, record):
        self.records[record.operation_id] = record

    def reserve_probe(self, *args):
        self.probes.append(args)

    def finish_probe(self, *args):
        self.finished.append(args)


class API:
    def __init__(self, journal):
        self.journal = journal
        self.objects = {"user": [], "meta": [], "storage": []}
        self.events = []
        self.timeout = False

    def call(self, method, path, body=None):
        resource, action = path.split("?")[0].split("/")[-2:]
        self.events.append((method, path, body))
        if action == "list":
            return {"content": self.objects[resource], "total": len(self.objects[resource])}
        if action == "create":
            assert self.journal.records, "ownership must be durable before create"
            if resource == "storage":
                assert next(iter(self.journal.records.values())).delivery_started
                assert self.objects["meta"], "guest isolation must precede storage"
            obj = dict(body, id=len(self.objects[resource]) + 1)
            self.objects[resource].append(obj)
            if resource == "storage" and self.timeout:
                self.timeout = False
                raise TimeoutError("response token-secret must not leak")
            return {"id": obj["id"]} if resource == "storage" else None
        if action == "update":
            obj = next(o for o in self.objects[resource] if o["id"] == body["id"])
            obj.update(body)
            return None
        if action == "delete":
            ident = int(path.split("id=")[-1])
            self.objects[resource] = [o for o in self.objects[resource] if o["id"] != ident]
            return None
        raise AssertionError(path)


@pytest.fixture
def setup(modules):
    admin, candidate = modules
    journal = Journal()
    api = API(journal)
    connector = admin.RegisteredConnector(uuid4(), "http://127.0.0.1:5244", "4.2.6", True)
    identity = lambda provider, tokens: SimpleNamespace(
        provider=provider, account_id="123456", display_name="private"
    )
    client = admin.OpenListAdmin(
        connector, api, journal, identity, candidate_context=lambda op: (op, 1)
    )
    return client, api, journal, candidate


def prepare(client, operation=None, provider="baidu"):
    return client.prepare(
        operation or uuid4(),
        provider,
        {"client_id": "own-id", "client_secret": "own-secret"},
        SimpleNamespace(access_token="old-access", refresh_token="old-refresh"),
    )


def test_owned_objects_precede_token_delivery_and_use_only_own_baidu_app(setup):
    client, api, journal, _ = setup
    access = prepare(client)
    row = journal.read(access.ref.operation_id)
    assert row.delivered
    assert row.raw_account_id == "123456"
    assert access.username != "admin"
    storage = api.objects["storage"][0]
    import json

    addition = json.loads(storage["addition"])
    assert addition["use_online_api"] is False
    assert addition["client_id"] == "own-id"
    assert addition["client_secret"] == "own-secret"
    assert storage["webdav_policy"] == "native_proxy"
    assert api.objects["meta"][0]["read_users"] == [1]
    assert "old-access" not in repr(access)


def builtin_source(provider="baidu"):
    return {
        "protocol_version": 2,
        "mode": "openlist_builtin_import",
        "auth_config_id": "openlist-builtin-import-v1:" + provider,
        "auth_config_revision": 1,
        "app_revision": None,
    }


def builtin_prepare(client, operation, provider="baidu", *, source=None):
    from datetime import UTC, datetime, timedelta

    from cloud_auth.provider_types import ImportedTokenGrant

    now = datetime.now(UTC)
    tokens = ImportedTokenGrant(
        "latest-access", "latest-refresh", None if provider == "baidu" else now + timedelta(hours=1),
        "unreported" if provider == "baidu" else "provider", now, provider,
    )
    return client.prepare(operation, provider, None, tokens,
                          source=builtin_source(provider) if source is None else source)


@pytest.mark.parametrize("provider", ["baidu", "115"])
def test_builtin_candidate_freezes_source_and_delivers_latest_tokens_without_app(setup, provider):
    client, api, journal, _ = setup
    source = builtin_source(provider)
    client.auth_source = lambda operation: source
    access = builtin_prepare(client, uuid4(), provider)
    record = journal.read(access.ref.operation_id)
    assert {key: getattr(record, key) for key in source} == source
    storage = api.objects["storage"][0]
    addition = json.loads(storage["addition"])
    assert addition["refresh_token"] == "latest-refresh"
    assert "client_id" not in addition and "client_secret" not in addition
    if provider == "baidu":
        assert addition["use_online_api"] is True
        assert addition["api_url_address"] == "https://api.oplist.org"
        assert addition["AccessToken"] == "latest-access"
    else:
        assert storage["driver"] == "115 Open"
        assert addition["access_token"] == "latest-access"
    # A connector may already have rotated tokens after taking ownership.
    addition["refresh_token"] = "connector-rotated"
    storage["addition"] = json.dumps(addition)
    builtin_prepare(client, access.ref.operation_id, provider)
    assert json.loads(storage["addition"])["refresh_token"] == "connector-rotated"
    assert len(api.objects["storage"]) == 1
    assert not any("storage/update" in path for _, path, _ in api.events)


@pytest.mark.parametrize("bad", [
    {"auth_config_id": "openlist-builtin-import-v1:115"},
    {"auth_config_revision": 2}, {"auth_config_revision": True},
    {"app_revision": 1}, {"protocol_version": 3}, {"mode": "own_app"},
    {"unknown": "rejected"},
])
def test_builtin_candidate_rejects_invalid_frozen_source_before_external_changes(setup, bad):
    client, api, journal, _ = setup
    client.auth_source = lambda operation: builtin_source()
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        builtin_prepare(client, uuid4(), source={**builtin_source(), **bad})
    assert api.events == [] and journal.records == {}


@pytest.mark.parametrize("source", ["own_app", [], 2])
def test_non_object_source_is_rejected_without_raw_error(setup, source):
    client, api, journal, _ = setup
    with pytest.raises(RuntimeError, match="^cloud_ownership_mismatch$"):
        builtin_prepare(client, uuid4(), source=source)
    assert api.events == [] and journal.records == {}


@pytest.mark.parametrize("consumer", ["reconcile", "cleanup_operation", "prepare"])
@pytest.mark.parametrize("mutation", ["source", "candidate_id", "config_revision"])
def test_candidate_saved_source_and_identity_cannot_be_overridden(setup, consumer, mutation):
    client, api, journal, _ = setup
    client.auth_source = lambda operation: builtin_source()
    operation = uuid4()
    builtin_prepare(client, operation)
    record = journal.read(operation)
    changes = {"auth_config_revision": 2} if mutation == "source" else {
        mutation: uuid4() if mutation == "candidate_id" else 9
    }
    # Simulate a corrupt persisted record, not a fresh valid constructor.
    from copy import copy
    corrupted = copy(record)
    for key, value in changes.items():
        object.__setattr__(corrupted, key, value)
    journal.records[operation] = corrupted
    events = len(api.events)
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        if consumer == "prepare":
            builtin_prepare(client, operation)
        else:
            getattr(client, consumer)(operation)
    assert api.events[events:] == []


def test_v2_candidate_cannot_resume_without_persisted_work_source(setup):
    client, api, _, _ = setup
    client.auth_source = lambda operation: builtin_source()
    operation = uuid4()
    builtin_prepare(client, operation)
    client.auth_source = None
    events = len(api.events)
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        client.cleanup_operation(operation)
    assert api.events[events:] == []


@pytest.mark.parametrize("provider", ["baidu", "115"])
def test_v2_own_app_candidate_preserves_direct_refresh(setup, provider):
    client, api, journal, _ = setup
    source = {"protocol_version": 2, "mode": "own_app", "auth_config_id": "own-app:" + provider,
              "auth_config_revision": 3, "app_revision": 3}
    client.auth_source = lambda operation: source
    access = client.prepare(uuid4(), provider, {"client_id": "own-id", "client_secret": "own-secret"},
                            SimpleNamespace(access_token="own-access", refresh_token="own-refresh"), source=source)
    assert journal.read(access.ref.operation_id).auth_source() == source
    addition = json.loads(api.objects["storage"][0]["addition"])
    if provider == "baidu":
        assert addition["use_online_api"] is False
        assert addition["api_url_address"] == ""
        assert addition["client_id"] == "own-id" and addition["client_secret"] == "own-secret"
    else:
        assert api.objects["storage"][0]["driver"] == "115 Open"


def test_builtin_candidate_rejects_untyped_tokens_before_external_changes(setup):
    client, api, journal, _ = setup
    client.auth_source = lambda operation: builtin_source()
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        client.prepare(uuid4(), "baidu", None, SimpleNamespace(access_token="unverified", refresh_token="unverified"),
                       source=builtin_source())
    assert api.events == [] and journal.records == {}


def test_new_explicit_source_cannot_claim_legacy_missing_config(setup):
    client, api, journal, _ = setup
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        client.prepare(uuid4(), "baidu", {"client_id": "own-id", "client_secret": "own-secret"},
                       SimpleNamespace(access_token="unverified", refresh_token="unverified"),
                       source={"protocol_version": 1, "mode": "own_app", "auth_config_id": None,
                               "auth_config_revision": None, "app_revision": None})
    assert api.events == [] and journal.records == {}


@pytest.mark.parametrize("context", [("not-a-uuid", 1), (uuid4(), True), (uuid4(), 0)])
def test_candidate_context_requires_real_identity_and_positive_strict_revision(setup, context):
    client, api, journal, _ = setup
    client.auth_source = lambda operation: builtin_source()
    client.candidate_context = lambda operation: context
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        builtin_prepare(client, uuid4())
    assert api.events == [] and journal.records == {}


@pytest.mark.parametrize("invalid", ["untyped", "wrong_provider", "unexpected_application"])
def test_reserved_pre_delivery_retry_revalidates_credentials_before_any_mutation(setup, invalid):
    from datetime import UTC, datetime, timedelta

    from cloud_auth.provider_types import ImportedTokenGrant

    client, api, journal, _ = setup
    client.auth_source = lambda operation: builtin_source()
    operation = uuid4()
    original = api.call

    def fail_before_delivery(method, path, body=None):
        if path == "/api/admin/user/create":
            raise TimeoutError("private external details")
        return original(method, path, body)

    api.call = fail_before_delivery
    with pytest.raises(RuntimeError, match="^cloud_connector_failed$"):
        builtin_prepare(client, operation)
    record = journal.read(operation)
    assert record is not None and not record.delivery_started and not record.delivered
    api.call = original
    now = datetime.now(UTC)
    tokens = ImportedTokenGrant("latest", "latest", None, "unreported", now, "baidu")
    application = None
    if invalid == "untyped":
        tokens = SimpleNamespace(access_token="unverified", refresh_token="unverified")
    elif invalid == "wrong_provider":
        tokens = ImportedTokenGrant("wrong-drive", "wrong-drive", now + timedelta(hours=1),
                                    "provider", now, "115")
    else:
        application = {"client_id": "unexpected", "client_secret": "unexpected"}
    events = len(api.events)
    with pytest.raises(RuntimeError, match="^cloud_ownership_mismatch$"):
        client.prepare(operation, "baidu", application, tokens, source=builtin_source())
    assert all(method == "GET" for method, _, _ in api.events[events:])
    assert api.objects == {"user": [], "meta": [], "storage": []}
    assert not journal.read(operation).delivery_started


def test_timeout_after_create_reconciles_without_resending_stale_tokens(setup):
    client, api, journal, _ = setup
    op = uuid4()
    api.timeout = True
    with pytest.raises(RuntimeError, match="cloud_delivery_unknown"):
        prepare(client, op)
    import json

    current = api.objects["storage"][0]
    current["addition"] = json.dumps({"access_token": "refreshed", "refresh_token": "refreshed"})
    ref = client.reconcile(op)
    assert ref.storage_id == "1"
    assert journal.read(op).delivered
    with pytest.raises(RuntimeError, match="cloud_delivery_unknown"):
        prepare(client, op)
    assert len(api.objects["storage"]) == 1
    assert json.loads(current["addition"])["access_token"] == "refreshed"
    assert not any("storage/update" in path for _, path, _ in api.events)


def test_unknown_delivery_with_missing_storage_never_resends(setup):
    client, api, _, _ = setup
    op = uuid4()
    prepare(client, op)
    api.objects["storage"].clear()
    with pytest.raises(RuntimeError, match="cloud_delivery_unknown"):
        prepare(client, op)
    assert not api.objects["storage"]


def test_cleanup_refuses_forged_or_modified_ownership(setup):
    client, api, _, _ = setup
    access = prepare(client)
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        client.cleanup(replace(access.ref, storage_id="77"))
    api.objects["storage"][0]["remark"] = "manual mount"
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        client.cleanup(access.ref)
    assert len(api.objects["storage"]) == 1


@pytest.mark.parametrize(
    "resource,identity_field", [("storage", "mount_path"), ("user", "username"), ("meta", "path")]
)
@pytest.mark.parametrize("mutation", ["relocated", "foreign_replacement", "duplicate_saved_id"])
@pytest.mark.parametrize(
    "consumer", ["verify_unbind", "cleanup_operation", "reconcile", "verify_access"]
)
def test_saved_ids_are_reconciled_globally_before_declaring_absence(
    setup, resource, identity_field, mutation, consumer
):
    from copy import deepcopy

    client, api, journal, _ = setup
    operation = uuid4()
    access = prepare(client, operation)
    client.select_directory(access.ref, "/backup")
    original = api.objects[resource][0]
    if mutation == "relocated":
        original[identity_field] = "/foreign" if resource != "user" else "renamed-user"
    elif mutation == "foreign_replacement":
        original["id"] = 999
    else:
        api.objects[resource].append(
            {**original, identity_field: "/foreign" if resource != "user" else "renamed-user"}
        )
    before = deepcopy(api.objects)
    event_count = len(api.events)
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        if consumer == "verify_unbind":
            client.verify_unbind(access.ref)
        elif consumer == "cleanup_operation":
            client.cleanup_operation(operation)
        elif consumer == "reconcile":
            client.reconcile(operation)
        else:
            client.verify_access(access.ref, "/backup")
    assert api.objects == before
    assert all(method == "GET" for method, _, _ in api.events[event_count:])
    assert journal.read(operation) is not None


def test_cleanup_deletes_only_owned_objects(setup):
    client, api, _, _ = setup
    access = prepare(client)
    api.objects["storage"].append({"id": 99, "mount_path": "/manual", "remark": "keep"})
    client.cleanup(access.ref)
    assert api.objects["storage"] == [{"id": 99, "mount_path": "/manual", "remark": "keep"}]
    assert api.objects["user"] == []
    assert api.objects["meta"] == []


def test_select_directory_changes_user_scope_not_storage_tokens(setup):
    client, api, _, _ = setup
    access = prepare(client)
    selected = client.select_directory(access.ref, "/中文 空格/100%")
    assert selected.directory == "/中文 空格/100%"
    assert api.objects["user"][0]["base_path"] == access.ref.mount_path + "/中文 空格/100%"
    assert not any("storage/update" in path for _, path, _ in api.events)


def test_literal_percent_sequences_are_not_decoded_into_different_names(setup):
    client, api, _, _ = setup
    access = prepare(client)
    result = client.select_directory(access.ref, "/literal%20space/100%25")
    assert result.directory == "/literal%20space/100%25"
    assert api.objects["user"][0]["base_path"] == access.ref.mount_path + "/literal%20space/100%25"


@pytest.mark.parametrize(
    "path",
    [
        "/../escape",
        "/%2e%2e/escape",
        "/%252e%252e",
        "//host/path",
        "https://evil/a",
        "/a\\b",
        "/a\x00b",
        "/a?token",
        "/a/.",
    ],
)
def test_directory_rejects_traversal_and_urls(setup, path):
    client, api, _, _ = setup
    access = prepare(client)
    count = len(api.events)
    with pytest.raises(RuntimeError, match="cloud_invalid_path"):
        client.select_directory(access.ref, path)
    assert len(api.events) == count


@pytest.mark.parametrize(
    "origin,version,managed",
    [
        ("https://example.com", "4.2.6", True),
        ("http://127.0.0.1:5244/dav", "4.2.6", True),
        ("http://127.0.0.1:5244", "latest", True),
        ("http://127.0.0.1:5244", "4.2.6", False),
    ],
)
def test_registry_rejects_unmanaged_or_nonprivate_connector(modules, origin, version, managed):
    with pytest.raises(RuntimeError, match="cloud_connector_unregistered"):
        modules[0].RegisteredConnector(uuid4(), origin, version, managed)


def test_admin_password_is_exchanged_before_authenticated_admin_request(modules, monkeypatch):
    admin, _ = modules
    calls = []
    responses = [
        (
            200,
            json.dumps(
                {"code": 200, "message": "success", "data": {"token": "derived-test-token"}}
            ).encode(),
        ),
        (
            200,
            json.dumps(
                {"code": 200, "message": "success", "data": {"content": [{"id": 7}], "total": 1}}
            ).encode(),
        ),
    ]

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 5244, 20)

        def request(self, method, path, body=None, headers=None):
            calls.append((method, path, body, headers))

        def getresponse(self):
            status, payload = responses.pop(0)
            return SimpleNamespace(status=status, read=lambda limit: payload[:limit])

        def close(self):
            pass

    monkeypatch.setattr(admin.http.client, "HTTPConnection", Connection)
    connector = admin.RegisteredConnector(uuid4(), "http://127.0.0.1:5244", "4.2.6", True)
    api = admin.OpenListAPI.from_admin_password(connector, "dummy-installed-password")
    assert api.call("GET", "/api/admin/user/list?page=1&per_page=100") == {
        "content": [{"id": 7}],
        "total": 1,
    }
    assert calls[0][:2] == ("POST", "/api/auth/login")
    assert json.loads(calls[0][2]) == {"username": "admin", "password": "dummy-installed-password"}
    assert "Authorization" not in calls[0][3]
    assert calls[1][3]["Authorization"] == "derived-test-token"
    assert "dummy-installed-password" not in repr(vars(api))


@pytest.mark.parametrize(
    "status,payload",
    [
        (302, b"secret-bearing redirect body"),
        (401, b"dummy-installed-password rejected"),
        (200, b"not-json dummy-installed-password"),
        (200, b"x" * 65537),
        (200, b'{"code":401,"message":"dummy-installed-password","data":null}'),
        (200, b'{"code":200,"data":{}}'),
        (200, b'{"code":200,"data":{"token":null}}'),
        (200, b'{"code":200,"data":{"token":"bad\\r\\nheader"}}'),
    ],
    ids=[
        "redirect",
        "unauthorized",
        "malformed",
        "oversized",
        "rejected",
        "missing-token",
        "null-token",
        "header-injection",
    ],
)
def test_admin_password_exchange_rejects_unsafe_responses_without_leak(
    modules, monkeypatch, status, payload
):
    admin, _ = modules

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return SimpleNamespace(status=status, read=lambda limit: payload[:limit])

        def close(self):
            pass

    monkeypatch.setattr(admin.http.client, "HTTPConnection", Connection)
    connector = admin.RegisteredConnector(uuid4(), "http://127.0.0.1:5244", "4.2.6", True)
    with pytest.raises(RuntimeError) as failure:
        admin.OpenListAPI.from_admin_password(connector, "dummy-installed-password")
    assert str(failure.value) == "cloud_connector_failed"


def test_admin_password_exchange_redacts_connection_failure(modules, monkeypatch):
    admin, _ = modules

    def unavailable(*args, **kwargs):
        raise OSError("dummy-installed-password connection failure")

    monkeypatch.setattr(admin.http.client, "HTTPConnection", unavailable)
    connector = admin.RegisteredConnector(uuid4(), "http://127.0.0.1:5244", "4.2.6", True)
    with pytest.raises(RuntimeError) as failure:
        admin.OpenListAPI.from_admin_password(connector, "dummy-installed-password")
    assert str(failure.value) == "cloud_connector_failed"


def test_missing_concrete_identity_fails_before_any_connector_mutation(setup):
    client, api, journal, _ = setup
    client.identity_resolver = lambda *_: SimpleNamespace(provider="baidu", account_id="")
    with pytest.raises(RuntimeError, match="cloud_account_identity_unknown"):
        prepare(client)
    assert not api.events
    assert not journal.records


def test_probe_journals_before_put_verifies_64_bytes_and_cleans_only_own_path(setup, tmp_path):
    client, _, journal, candidate = setup
    access = client.select_directory(prepare(client).ref, "/中文 空格/100%")
    files = {"/keep": b"untouched"}

    class Dav:
        def put(self, path, source):
            assert journal.probes and journal.probes[0][3] == path
            files[path] = source.read_bytes()

        def get(self, path, destination, limit):
            assert len(files[path]) == limit == 64
            destination.write_bytes(files[path])

        def delete(self, path):
            del files[path]

    evidence = candidate.probe_candidate(
        access,
        access.directory,
        uuid4(),
        journal=journal,
        transport=Dav(),
        scope_checker=lambda *_: None,
    )
    assert evidence.directory == "/中文 空格/100%"
    assert evidence.candidate_id == access.candidate_id
    assert len(evidence.sha256) == 64
    assert files == {"/keep": b"untouched"}
    assert journal.finished[-1][1:] == (None, False)


def test_probe_preserves_corruption_error_when_delete_fails(setup):
    client, _, journal, candidate = setup
    access = client.select_directory(prepare(client).ref, "/backup")

    class Dav:
        def put(self, path, source):
            pass

        def get(self, path, destination, limit):
            destination.write_bytes(b"wrong")

        def delete(self, path):
            raise RuntimeError("secret delete body")

    with pytest.raises(RuntimeError, match="cloud_readback_failed"):
        candidate.probe_candidate(
            access,
            "/backup",
            uuid4(),
            journal=journal,
            transport=Dav(),
            scope_checker=lambda *_: None,
        )
    assert journal.finished[-1][1:] == ("cloud_readback_failed", True)


def test_probe_rejects_directory_change_before_network(setup):
    client, _, journal, candidate = setup
    access = client.select_directory(prepare(client).ref, "/backup")
    with pytest.raises(RuntimeError, match="cloud_probe_stale"):
        candidate.probe_candidate(access, "/other", uuid4(), journal=journal)
    assert not journal.probes


def test_cleanup_can_resume_after_user_delete_before_meta_delete(setup):
    client, api, _, _ = setup
    access = prepare(client)
    api.objects["storage"].clear()
    api.objects["user"].clear()
    client.cleanup(access.ref)
    assert not api.objects["meta"]


def test_directory_listing_preserves_names_without_interpreting_urls(setup):
    client, api, _, _ = setup
    access = prepare(client)
    original = api.call

    def call(method, path, body=None):
        if path == "/api/fs/list":
            assert body["path"] == access.ref.mount_path + "/中文 空格"
            return {
                "content": [{"name": "100%", "is_dir": True}, {"name": "file", "is_dir": False}]
            }
        return original(method, path, body)

    api.call = call
    entries = client.list_directories(access.ref, "/中文 空格")
    assert [(e.path, e.name, e.selectable) for e in entries] == [("/中文 空格/100%", "100%", True)]


def test_probe_blocks_without_proven_negative_scope(setup):
    client, _, journal, candidate = setup
    access = client.select_directory(prepare(client).ref, "/backup")

    class Dav:
        def put(self, *args):
            pytest.fail("must never upload after negative scope failure")

        def delete(self, *args):
            pass

    def deny(*args):
        raise RuntimeError("secret status body")

    with pytest.raises(RuntimeError, match="cloud_scope_unverified"):
        candidate.probe_candidate(
            access, "/backup", uuid4(), journal=journal, transport=Dav(), scope_checker=deny
        )
    assert journal.finished[-1][1:] == ("cloud_scope_unverified", False)


def test_unknown_directory_update_cannot_retry_same_intent_or_release_access(setup):
    client, api, journal, _ = setup
    access = prepare(client)
    original = api.call

    def fail(method, path, body=None):
        if path == "/api/admin/user/update":
            raise TimeoutError("request not yet delivered")
        return original(method, path, body)

    api.call = fail
    with pytest.raises(RuntimeError, match="cloud_connector_failed"):
        client.select_directory(access.ref, "/backup")
    api.call = original
    with pytest.raises(RuntimeError, match="cloud_directory_delivery_unknown"):
        client.select_directory(access.ref, "/backup")
    assert api.objects["user"][0]["base_path"] == access.ref.mount_path
    assert journal.read(access.ref.operation_id).scope_pending
    assert not any("storage/update" in path for _, path, _ in api.events)


def test_partial_prepare_objects_can_be_cleaned_by_owned_operation(setup):
    client, api, journal, _ = setup
    access = prepare(client)
    api.objects["storage"].clear()
    journal.save(
        replace(
            journal.read(access.ref.operation_id),
            storage_id=None,
            delivered=False,
            delivery_started=False,
        )
    )
    client.cleanup_operation(access.ref.operation_id)
    assert api.objects["user"] == []
    assert api.objects["meta"] == []


def test_late_storage_create_never_loses_protective_objects_during_cleanup(setup):
    client, api, journal, _ = setup
    original = api.call
    held = []

    def defer_create(method, path, body=None):
        if path == "/api/admin/storage/create":
            held.append(dict(body))
            raise TimeoutError("original mutation still running")
        return original(method, path, body)

    api.call = defer_create
    operation = uuid4()
    with pytest.raises(RuntimeError, match="cloud_delivery_unknown"):
        prepare(client, operation)
    api.call = original
    with pytest.raises(RuntimeError, match="cloud_cleanup_pending"):
        client.cleanup_operation(operation)
    assert len(api.objects["user"]) == len(api.objects["meta"]) == 1
    assert api.objects["storage"] == []
    api.objects["storage"].append(dict(held[0], id=1))
    assert client.reconcile(operation).storage_id == "1"
    assert journal.read(operation).creation_unknown
    with pytest.raises(RuntimeError, match="cloud_cleanup_pending"):
        client.cleanup_operation(operation)
    assert len(api.objects["user"]) == len(api.objects["meta"]) == len(api.objects["storage"]) == 1
    with pytest.raises(RuntimeError, match="cloud_delivery_unknown"):
        prepare(client, operation)


def test_late_directory_update_cannot_overwrite_a_later_released_scope(setup):
    client, api, journal, _ = setup
    access = prepare(client)
    original = api.call
    held = []

    def defer_update(method, path, body=None):
        if path == "/api/admin/user/update":
            held.append(dict(body))
            raise TimeoutError("original scope mutation still running")
        return original(method, path, body)

    api.call = defer_update
    with pytest.raises(RuntimeError, match="cloud_connector_failed"):
        client.select_directory(access.ref, "/A")
    api.call = original
    for directory in ("/A", "/B"):
        with pytest.raises(RuntimeError, match="cloud_directory_delivery_unknown"):
            client.select_directory(access.ref, directory)
    assert len(held) == 1
    api.objects["user"][0].update(held[0])
    client.reconcile(access.ref.operation_id)
    assert journal.read(access.ref.operation_id).scope_pending
    with pytest.raises(RuntimeError, match="cloud_directory_delivery_unknown"):
        client.select_directory(access.ref, "/B")
    with pytest.raises(RuntimeError, match="cloud_directory_delivery_unknown"):
        prepare(client, access.ref.operation_id)
    with pytest.raises(RuntimeError, match="cloud_cleanup_pending"):
        client.cleanup_operation(access.ref.operation_id)
    assert len(api.objects["user"]) == len(api.objects["meta"]) == len(api.objects["storage"]) == 1


def test_foreign_nested_mount_blocks_candidate_without_deleting_foreign_objects(setup):
    client, api, _, _ = setup
    access = prepare(client)
    api.objects["storage"].append(
        {"id": 99, "mount_path": access.ref.mount_path + "/foreign", "remark": "manual"}
    )
    with pytest.raises(RuntimeError, match="cloud_ownership_mismatch"):
        client.cleanup(access.ref)
    assert len(api.objects["storage"]) == 2


@pytest.mark.skipif(
    os.environ.get("ERP_TEST_OPENLIST_426") != "1",
    reason="opt-in isolated OpenList 4.2.6, never a live connector",
)
def test_pinned_openlist_real_directory_scope_and_probe(modules, tmp_path):
    """Real installed-password runner auth and Local scope; no provider accounts."""
    admin, candidate = modules
    project = "erp-openlist-test-" + uuid4().hex
    name = project + "-cloud-connector"
    install_spec = importlib.util.spec_from_file_location(
        "connector_installer_test", ROOT / "scripts/erp-cloud-connector-install.py"
    )
    installer = importlib.util.module_from_spec(install_spec)
    install_spec.loader.exec_module(installer)
    runner_spec = importlib.util.spec_from_file_location(
        "connector_runner_test", ROOT / "scripts/erp-cloud-authorize.py"
    )
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)

    def request(method, path, body=None, authorization=""):
        connection = http.client.HTTPConnection("127.0.0.1", 5244, timeout=5)
        try:
            encoded = json.dumps(body).encode() if isinstance(body, dict) else body
            connection.request(
                method,
                path,
                encoded,
                {"Authorization": authorization, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = response.read()
            return response.status, payload
        finally:
            connection.close()

    try:
        connector_id = str(uuid4())
        installer.create(tmp_path, project=project, connector_id=connector_id)
        registry_path = tmp_path / "cloud-connector/registry.json"
        registry = json.loads(registry_path.read_text())
        secret_path = Path(registry["admin_secret_file"])
        secret_digest = hashlib.sha256(secret_path.read_bytes()).digest()
        # Use the real entry point and protected registry; no premocked JWT/API.
        managed = runner.connector_admin(
            {"app_dir": str(tmp_path)},
            {"candidate": {"connector_id": connector_id, "id": str(uuid4()), "config_revision": 1}},
            Journal(),
        )
        connector, api = managed.connector, managed.api
        users = api.call("GET", "/api/admin/user/list?page=1&per_page=100")
        assert any(user["role"] == 2 for user in users["content"])
        assert hashlib.sha256(secret_path.read_bytes()).digest() == secret_digest
        assert hashlib.sha256(api._secret.encode()).digest() != secret_digest
        api.call(
            "POST",
            "/api/admin/user/create",
            {
                "username": "candidate",
                "password": "isolated-test-only",
                "base_path": "/test/中文 空格/100%",
                "role": 0,
                "permission": admin.PERMISSION,
                "disabled": False,
                "allow_ldap": False,
            },
        )
        user_id = next(
            u["id"]
            for u in api.call("GET", "/api/admin/user/list?page=1&per_page=100")["content"]
            if u["username"] == "candidate"
        )
        api.call(
            "POST",
            "/api/admin/meta/create",
            {
                "path": "/test",
                "password": "isolated-meta",
                "p_sub": True,
                "read_users": [user_id],
                "write_users": [user_id],
                "read_users_sub": True,
                "write_users_sub": True,
            },
        )
        (Path(registry["data_dir"]) / "test-sandbox").mkdir(mode=0o700)
        api.call(
            "POST",
            "/api/admin/storage/create",
            {
                "mount_path": "/test",
                "driver": "Local",
                "addition": json.dumps(
                    {
                        "root_folder_path": "/opt/openlist/data/test-sandbox",
                        "mkdir_perm": "700",
                        "recycle_bin_path": "delete permanently",
                    }
                ),
                "webdav_policy": "native_proxy",
            },
        )
        # Only the fresh pytest temporary installer directory is touched.
        subprocess.run(
            [
                "docker",
                "exec",
                name,
                "mkdir",
                "-p",
                "/opt/openlist/data/test-sandbox/中文 空格/100%",
                "/opt/openlist/data/test-sandbox/outside",
            ],
            check=True,
            capture_output=True,
        )
        op = uuid4()
        ref = candidate.CandidateRef(op, "1", str(user_id), "/test", "opaque-test-identity")
        access = candidate.CandidateAccess(
            ref, "candidate", "isolated-test-only", connector.origin, "/中文 空格/100%", op, 1
        )
        journal = Journal()
        evidence = candidate.probe_candidate(access, access.directory, uuid4(), journal=journal)
        assert evidence.directory == "/中文 空格/100%"
        assert journal.finished[-1][1:] == (None, False)
        credential = "Basic " + base64.b64encode(b"candidate:isolated-test-only").decode()
        for method in ("GET", "PUT", "MKCOL"):
            status, _ = request(
                method, "/dav/%2e%2e/outside", b"" if method == "PUT" else None, credential
            )
            assert status == 403
        # Enabled guest must still be denied by candidate metadata before mount reads.
        guest = next(
            u
            for u in api.call("GET", "/api/admin/user/list?page=1&per_page=100")["content"]
            if u["role"] == 1
        )
        api.call("POST", "/api/admin/user/update", dict(guest, disabled=False, password=""))
        _, response = request(
            "POST", "/api/fs/list", {"path": "/test", "password": "", "page": 1, "per_page": 100}
        )
        assert json.loads(response)["code"] == 403
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

"""Fixed OpenList 4.2.6 API, never an arbitrary management proxy.

Storage creation is the only token delivery. Its unknown outcome is reconciled,
never retried with old tokens. The encrypted backend journal owns durability.
"""

import hashlib
import http.client
import json
import secrets
from dataclasses import dataclass, replace
from uuid import UUID

from erp_cloud_transport import CloudError

from .candidate import (
    CandidateAccess,
    CandidateOwnership,
    CandidateRef,
    DirectoryEntry,
    logical_directory,
    validate_auth_source,
)
from .provider_types import ImportedTokenGrant

PERMISSION = 2 + 8 + 128 + 256 + 512


@dataclass(frozen=True)
class RegisteredConnector:
    connector_id: UUID
    origin: str
    version: str
    managed: bool

    def __post_init__(self):
        if (
            not isinstance(self.connector_id, UUID)
            or self.origin != "http://127.0.0.1:5244"
            or self.version != "4.2.6"
            or self.managed is not True
        ):
            raise CloudError("cloud_connector_unregistered")


class OpenListAPI:
    """Bounded JSON, fixed loopback, no redirects/proxies; secret is host-only."""

    def __init__(self, connector: RegisteredConnector, admin_secret: str):
        self.connector = connector
        self._secret = admin_secret
        if not admin_secret or any(c in admin_secret for c in "\r\n"):
            raise CloudError("cloud_connector_unregistered")

    @classmethod
    def from_admin_password(
        cls, connector: RegisteredConnector, password: str
    ) -> "OpenListAPI":
        """Exchange the installed password; retain only the JWT in memory.

        OpenList's Authorization header accepts a token, not the administrator
        password. This host-only exchange has no configurable URL or redirects.
        """
        if not isinstance(connector, RegisteredConnector):
            raise CloudError("cloud_connector_unregistered")
        if not isinstance(password, str) or not password or len(password) > 4096:
            raise CloudError("cloud_connector_failed")
        connection = None
        try:
            payload = json.dumps({"username": "admin", "password": password}).encode()
            if len(payload) > 65536:
                raise CloudError("cloud_connector_failed")
            connection = http.client.HTTPConnection("127.0.0.1", 5244, timeout=20)
            connection.request(
                "POST", "/api/auth/login", payload, {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise CloudError("cloud_connector_failed")
            raw = response.read(65537)
            if len(raw) > 65536:
                raise CloudError("cloud_connector_failed")
            result = json.loads(raw)
            if (
                not isinstance(result, dict)
                or result.get("code") != 200
                or not isinstance(result.get("data"), dict)
            ):
                raise CloudError("cloud_connector_failed")
            token = result["data"].get("token")
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 8192
                or any(ord(c) < 32 or ord(c) == 127 for c in token)
            ):
                raise CloudError("cloud_connector_failed")
            return cls(connector, token)
        except (OSError, ValueError, http.client.HTTPException):
            raise CloudError("cloud_connector_failed") from None
        finally:
            if connection is not None:
                connection.close()

    def call(self, method, path, body=None):
        if not path.startswith(
            (
                "/api/admin/user/",
                "/api/admin/meta/",
                "/api/admin/storage/",
                "/api/fs/list",
                "/api/fs/get",
            )
        ) or method not in ("GET", "POST"):
            raise CloudError("cloud_connector_failed")
        connection = http.client.HTTPConnection("127.0.0.1", 5244, timeout=20)
        try:
            encoded = json.dumps(body).encode() if body is not None else None
            if encoded is not None and len(encoded) > 65536:
                raise CloudError("cloud_connector_failed")
            connection.request(
                method,
                path,
                encoded,
                {"Authorization": self._secret, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise CloudError("cloud_connector_failed")
            data = response.read(65537)
            if len(data) > 65536:
                raise CloudError("cloud_connector_failed")
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("code") != 200:
                raise CloudError("cloud_connector_failed")
            return result.get("data")
        except (OSError, ValueError, http.client.HTTPException):
            raise CloudError("cloud_connector_failed") from None
        finally:
            connection.close()


class OpenListAdmin:
    def __init__(
        self, connector, api, journal, identity_resolver, *, candidate_context, auth_source=None
    ):
        self.connector, self.api, self.journal = connector, api, journal
        self.identity_resolver, self.candidate_context = (
            identity_resolver,
            candidate_context,
        )
        self.auth_source = auth_source

    def _call(self, *args):
        try:
            return self.api.call(*args)
        except Exception:  # noqa: BLE001 -- private boundary: never expose HTTP bodies
            raise CloudError("cloud_connector_failed") from None

    def _list(self, resource):
        result = []
        for page in range(1, 101):
            data = self._call(
                "GET", f"/api/admin/{resource}/list?page={page}&per_page=100"
            )
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("content"), list)
                or not isinstance(data.get("total"), int)
            ):
                raise CloudError("cloud_connector_failed")
            result.extend(data["content"])
            if len(result) >= data["total"]:
                return result
        raise CloudError("cloud_connector_failed")

    def _record(self, operation_id):
        record = self.journal.read(operation_id)
        if (record is None or record.connector_id != self.connector.connector_id
                or record.operation_id != operation_id):
            raise CloudError("cloud_ownership_mismatch")
        expected = (
            f"/erp-candidates/{self.connector.connector_id.hex}/{operation_id.hex}"
        )
        if record.mount_path != expected:
            raise CloudError("cloud_ownership_mismatch")
        source = validate_auth_source(record.provider, record.auth_source())
        if self.auth_source is None:
            if source["protocol_version"] != 1:
                raise CloudError("cloud_ownership_mismatch")
        elif validate_auth_source(record.provider, self.auth_source(operation_id)) != source:
            raise CloudError("cloud_ownership_mismatch")
        candidate_id, revision = self.candidate_context(operation_id)
        if (not isinstance(candidate_id, UUID) or type(revision) is not int or revision < 1
                or candidate_id != record.candidate_id) or (
            source["protocol_version"] == 2
            and record.config_revision not in (
                revision, revision + 1 if record.scope_pending else revision
            )
        ):
            raise CloudError("cloud_ownership_mismatch")
        return record

    def _objects(self, record, *, allow_missing_protection=False):
        all_users = self._list("user")
        all_metas, all_storages = self._list("meta"), self._list("storage")
        # Saved IDs are authoritative across the complete lists. Filtering by
        # the old name/path first would misclassify a relocated survivor as absent.
        for items, saved_id, identity_key, expected_identity in (
            (all_users, record.webdav_user_id, "username", record.username),
            (all_metas, record.meta_id, "path", record.mount_path),
            (all_storages, record.storage_id, "mount_path", record.mount_path),
        ):
            if saved_id is not None:
                saved = [item for item in items if str(item.get("id")) == saved_id]
                if len(saved) > 1 or (
                    saved and saved[0].get(identity_key) != expected_identity
                ):
                    raise CloudError("cloud_ownership_mismatch")
        users = [u for u in all_users if u.get("username") == record.username]
        for items, key in ((all_metas, "path"), (all_storages, "mount_path")):
            if any(
                str(obj.get(key, ""))
                .casefold()
                .startswith(record.mount_path.casefold() + "/")
                for obj in items
            ):
                raise CloudError("cloud_ownership_mismatch")
        metas = [
            m
            for m in all_metas
            if str(m.get("path", "")).casefold() == record.mount_path.casefold()
        ]
        storages = [
            s
            for s in all_storages
            if str(s.get("mount_path", "")).casefold() == record.mount_path.casefold()
        ]
        if max(len(users), len(metas), len(storages)) > 1:
            raise CloudError("cloud_ownership_mismatch")
        user, meta, storage = (
            items[0] if items else None for items in (users, metas, storages)
        )
        expected_base = record.mount_path + (
            record.directory if record.directory not in (None, "/") else ""
        )
        bases = {expected_base}
        if record.scope_pending:
            bases.add(
                record.mount_path
                + (
                    record.scope_previous
                    if record.scope_previous not in (None, "/")
                    else ""
                )
            )
        if user and (
            user.get("role") != 0
            or user.get("permission") != PERMISSION
            or user.get("base_path") not in bases
            or user.get("disabled") is not False
            or user.get("allow_ldap") is not False
        ):
            raise CloudError("cloud_ownership_mismatch")
        user_id = (
            user["id"]
            if user
            else (int(record.webdav_user_id) if record.webdav_user_id else None)
        )
        if meta and (
            meta.get("path") != record.mount_path
            or user_id is None
            or meta.get("password") != record.marker
            or meta.get("read_users") != [user_id]
            or meta.get("write_users") != [user_id]
            or meta.get("read_users_sub") is not True
            or meta.get("write_users_sub") is not True
            or meta.get("p_sub") is not True
        ):
            raise CloudError("cloud_ownership_mismatch")
        if storage and (
            storage.get("mount_path") != record.mount_path
            or (not meta and not allow_missing_protection)
            or storage.get("remark") != record.marker
            or storage.get("driver")
            != {"baidu": "BaiduNetdisk", "115": "115 Open"}.get(record.provider)
            or storage.get("webdav_policy") != "native_proxy"
        ):
            raise CloudError("cloud_ownership_mismatch")
        for obj, saved_id in (
            (user, record.webdav_user_id),
            (meta, record.meta_id),
            (storage, record.storage_id),
        ):
            if obj and saved_id is not None and str(obj["id"]) != saved_id:
                raise CloudError("cloud_ownership_mismatch")
        return user, meta, storage

    def _ref(self, record):
        return CandidateRef(
            record.operation_id,
            record.storage_id,
            record.webdav_user_id,
            record.mount_path,
            record.account_fingerprint,
        )

    def _access(self, record):
        if record.creation_unknown:
            raise CloudError("cloud_delivery_unknown")
        if record.scope_pending:
            raise CloudError("cloud_directory_delivery_unknown")
        return CandidateAccess(
            self._ref(record),
            record.username,
            record.password,
            self.connector.origin,
            record.directory,
            record.candidate_id,
            record.config_revision,
        )

    def reconcile(self, operation_id):
        record = self.journal.read(operation_id)
        if record is None:
            return None
        record = self._record(operation_id)
        user, meta, storage = self._objects(record)
        record = replace(
            record,
            webdav_user_id=str(user["id"]) if user else record.webdav_user_id,
            meta_id=str(meta["id"]) if meta else record.meta_id,
            storage_id=str(storage["id"]) if storage else record.storage_id,
            delivered=bool(storage),
        )
        # Observation is not a mutation-completion fence: the original request
        # may still commit after this read. Never clear unknown-create/scope flags.
        self.journal.save(record)
        return self._ref(record) if storage else None

    def _validate_delivery(self, provider, application, tokens, source):
        if source["mode"] == "openlist_builtin_import" and (
            application is not None or not isinstance(tokens, ImportedTokenGrant)
            or tokens.provider != provider or tokens.mode != source["mode"]
        ):
            raise CloudError("cloud_ownership_mismatch")
        if source["mode"] == "own_app" and provider == "baidu" and (
            not isinstance(application, dict) or not application.get("client_id")
            or not application.get("client_secret")
        ):
            raise CloudError("cloud_application_unconfigured")

    def prepare(self, operation_id, provider, application, tokens, *, source=None):
        if not isinstance(operation_id, UUID) or provider not in ("baidu", "115"):
            raise CloudError("cloud_provider_unsupported")
        if source is not None and (not isinstance(source, dict) or source.get("protocol_version") != 2):
            raise CloudError("cloud_ownership_mismatch")
        # An absent source is the deliberately retained legacy call adapter.
        source = validate_auth_source(provider, source if source is not None else {
            "protocol_version": 1, "mode": "own_app", "auth_config_id": None,
            "auth_config_revision": None, "app_revision": None,
        })
        if self.auth_source is None:
            if source["protocol_version"] != 1:
                raise CloudError("cloud_ownership_mismatch")
        elif validate_auth_source(provider, self.auth_source(operation_id)) != source:
            raise CloudError("cloud_ownership_mismatch")
        previous = self.journal.read(operation_id)
        if previous is not None:
            if previous.provider != provider or previous.auth_source() != source:
                raise CloudError("cloud_ownership_mismatch")
            self.reconcile(operation_id)
            previous = self._record(operation_id)
            if previous.delivered:
                return self._access(previous)
            if previous.delivery_started:
                raise CloudError("cloud_delivery_unknown")
            record = previous
            self._validate_delivery(provider, application, tokens, source)
        else:
            self._validate_delivery(provider, application, tokens, source)
            identity = self.identity_resolver(provider, tokens)
            if (
                identity.provider != provider
                or not isinstance(identity.account_id, str)
                or not identity.account_id
                or len(identity.account_id) > 256
            ):
                raise CloudError("cloud_account_identity_unknown")
            candidate_id, revision = self.candidate_context(operation_id)
            record = CandidateOwnership(
                operation_id,
                self.connector.connector_id,
                candidate_id,
                revision,
                provider,
                f"/erp-candidates/{self.connector.connector_id.hex}/{operation_id.hex}",
                f"erp_{operation_id.hex}_{secrets.token_hex(8)}",
                secrets.token_urlsafe(32),
                secrets.token_urlsafe(32),
                hashlib.sha256(
                    f"{provider}:{identity.account_id}".encode()
                ).hexdigest(),
                identity.account_id,
                **source,
            )
            record = self.journal.reserve(record)
            self._record(operation_id)
        user, meta, storage = self._objects(record)
        if storage:
            self.reconcile(operation_id)
            return self._access(self._record(operation_id))
        if not user:
            self._call(
                "POST",
                "/api/admin/user/create",
                {
                    "username": record.username,
                    "password": record.password,
                    "base_path": record.mount_path,
                    "role": 0,
                    "permission": PERMISSION,
                    "disabled": False,
                    "allow_ldap": False,
                },
            )
            user, _, _ = self._objects(record)
            if user is None:
                raise CloudError("cloud_connector_failed")
        record = replace(record, webdav_user_id=str(user["id"]))
        self.journal.save(record)
        if not meta:
            self._call(
                "POST",
                "/api/admin/meta/create",
                {
                    "path": record.mount_path,
                    "password": record.marker,
                    "p_sub": True,
                    "read_users": [user["id"]],
                    "write_users": [user["id"]],
                    "read_users_sub": True,
                    "write_users_sub": True,
                },
            )
            _, meta, _ = self._objects(record)
            if meta is None:
                raise CloudError("cloud_connector_failed")
        addition = {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "root_folder_id": "0",
        }
        if provider == "baidu":
            addition = {
                "AccessToken": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "root_folder_path": "/",
                "use_online_api": source["mode"] == "openlist_builtin_import",
                "api_url_address": "https://api.oplist.org" if source["mode"] == "openlist_builtin_import" else "",
                "download_api": "official",
                "upload_api": "https://d.pcs.baidu.com",
                "upload_thread": "1",
            }
            if source["mode"] == "own_app":
                addition.update(client_id=application["client_id"], client_secret=application["client_secret"])
        record = replace(
            record,
            meta_id=str(meta["id"]),
            delivery_started=True,
            creation_unknown=True,
        )
        self.journal.save(record)
        try:
            self._call(
                "POST",
                "/api/admin/storage/create",
                {
                    "mount_path": record.mount_path,
                    "driver": {"baidu": "BaiduNetdisk", "115": "115 Open"}[provider],
                    "remark": record.marker,
                    "addition": json.dumps(addition),
                    "webdav_policy": "native_proxy",
                    "web_proxy": True,
                    "disabled": False,
                    "disable_index": True,
                },
            )
        except CloudError:
            raise CloudError("cloud_delivery_unknown") from None
        # Only the actual response proves the originating mutation completed.
        record = replace(record, creation_unknown=False)
        self.journal.save(record)
        if self.reconcile(operation_id) is None:
            raise CloudError("cloud_delivery_unknown")
        return self._access(self._record(operation_id))

    def _owned_ref(self, ref):
        record = self._record(ref.operation_id)
        if self._ref(record) != ref:
            raise CloudError("cloud_ownership_mismatch")
        return record

    def verify_access(self, ref, directory):
        """Read-only host proof immediately before an ERP reference switch."""
        record = self._owned_ref(ref)
        if record.creation_unknown or record.scope_pending or not record.delivered:
            raise CloudError("cloud_delivery_unknown")
        if record.directory != logical_directory(directory):
            raise CloudError("cloud_ownership_mismatch")
        if any(item is None for item in self._objects(record)):
            raise CloudError("cloud_ownership_mismatch")
        return self._access(record)

    def verify_unbind(self, ref):
        """Known absence is safe to detach; every surviving object stays fenced."""
        record = self._owned_ref(ref)
        if (
            record.creation_unknown
            or record.scope_pending
            or (record.delivery_started and not record.delivered)
        ):
            raise CloudError("cloud_cleanup_pending")
        self._objects(record, allow_missing_protection=True)

    def select_directory(self, ref, directory):
        directory = logical_directory(directory)
        record = self._owned_ref(ref)
        if record.creation_unknown:
            raise CloudError("cloud_delivery_unknown")
        if record.scope_pending:
            raise CloudError("cloud_directory_delivery_unknown")
        user, _, storage = self._objects(record)
        if user is None or storage is None:
            raise CloudError("cloud_connector_failed")
        # Intent is committed before external mutation; restart reconciles intent.
        record = replace(
            record,
            directory=directory,
            config_revision=record.config_revision + int(directory != record.directory),
            scope_previous=record.directory,
            scope_pending=True,
        )
        self.journal.save(record)
        user = dict(
            user,
            password="",
            base_path=record.mount_path + (directory if directory != "/" else ""),
        )
        self._call("POST", "/api/admin/user/update", user)
        actual_user, _, _ = self._objects(record)
        if actual_user is None or actual_user.get("base_path") != user["base_path"]:
            raise CloudError("cloud_directory_delivery_unknown")
        record = replace(record, scope_pending=False, scope_previous=None)
        self.journal.save(record)
        return self._access(record)

    def list_directories(self, ref, parent):
        parent = logical_directory(parent)
        record = self._owned_ref(ref)
        self._objects(record)
        data = self._call(
            "POST",
            "/api/fs/list",
            {
                "path": record.mount_path + (parent if parent != "/" else ""),
                "password": "",
                "page": 1,
                "per_page": 0,
                "refresh": True,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise CloudError("cloud_connector_failed")
        entries = []
        for obj in data["content"]:
            if obj.get("is_dir") is True:
                name = obj.get("name")
                if not isinstance(name, str) or "/" in name:
                    raise CloudError("cloud_invalid_path")
                path = logical_directory(parent.rstrip("/") + "/" + name)
                entries.append(DirectoryEntry(path, name, True))
        return entries

    def cleanup(self, ref):
        record = self._owned_ref(ref)
        self._cleanup(record)

    def cleanup_operation(self, operation_id):
        """System-only cleanup of partial prepares, still journal-fenced by caller."""
        self._cleanup(self._record(operation_id))

    def _cleanup(self, record):
        # Ordinary upstream writes have no cancellation/CAS. A late create can
        # expose the drive after meta deletion, and a late user Save can recreate
        # deleted credentials. Preserve the isolation barrier until supervised
        # quiescence resolves the originating mutation; do not infer it from list.
        if (
            record.creation_unknown
            or record.scope_pending
            or (record.delivery_started and not record.delivered)
        ):
            raise CloudError("cloud_cleanup_pending")
        user, meta, storage = self._objects(record, allow_missing_protection=True)
        # Validate all three BEFORE deleting anything, and never touch remote files.
        for resource, obj in (("storage", storage), ("user", user), ("meta", meta)):
            if obj is not None:
                self._call("POST", f"/api/admin/{resource}/delete?id={obj['id']}")

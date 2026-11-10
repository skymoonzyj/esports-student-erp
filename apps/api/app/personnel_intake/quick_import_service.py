"""Private preview and atomic new-person housing intake; never merges existing people."""

import hashlib
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from fastapi import Request
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.time import BUSINESS_TIME_ZONE, business_date_at
from app.core.transactions import atomic_write_transaction
from app.housing.access import HousingAccessService
from app.housing.api_support import LockedHousingAuthorizationDenied, _audit
from app.housing.errors import HousingServiceError
from app.housing.history_validity import effective_stay_clause
from app.housing.models import HousingBuilding, HousingRoom, HousingStay
from app.housing.occupancy_router import _authorize_locked_room, _require_current_care_actor
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.resident_readiness import HousingResidentReadinessPolicy
from app.housing.schemas import StayCreate
from app.identity.models import Person
from app.identity.normalization import IdentityValidationError, normalize_cn_id, normalize_phone
from app.identity.readiness import is_minor
from app.personnel_intake.approval_service import PersonnelApprovalService
from app.personnel_intake.identity_selection import (
    ManagedIdentityInput,
    StaffRole,
    StaffTeamSelection,
    resolve_managed_identity,
)
from app.personnel_intake.quick_import_models import QuickIntakeReceipt
from app.personnel_intake.quick_import_parser import ParsedImportRow, parse_table
from app.personnel_intake.quick_import_schemas import (
    CommitInput,
    CommitResult,
    ImportTicket,
    PreviewInput,
    PreviewResult,
    PreviewRow,
    QuickIdentity,
)
from app.personnel_intake.role_intent_models import IntakeRoleIntent
from app.personnel_intake.role_intent_policy import require_direct_intake
from app.personnel_intake.schemas import PendingIntakeInput
from app.security.encryption import EncryptedFieldError
from app.team_management.person_staff_query import PersonStaffQuery
from app.team_management.person_staff_schemas import StaffTeamOption

_AAD = "personnel-quick-import-v1"
_IDENTITIES: tuple[tuple[str, QuickIdentity], ...] = (
    ("队伍经理", "team_manager"), ("战队经理", "team_manager"), ("领队", "team_manager"),
    ("助教", "assistant_coach"), ("教练", "coach"), ("队员", "team_member"),
    ("学员", "student"), ("行政", "administration"), ("生活老师", "life_teacher"),
    ("物业", "property"), ("客服", "customer_service"), ("其他工作人员", "staff"),
    ("其他员工", "staff"), ("工作人员", "staff"),
)
_HOUSING_ERRORS = {
    "housing_room_pending_cleaning": "房间退房待保洁，请先完成保洁",
    "housing_parent_inactive": "房间所在楼栋或楼层已停用",
    "housing_room_unavailable": "房间不可用（维修、停用或功能间）",
    "housing_room_not_verified": "房间待核对，请先核对房间",
    "housing_capacity_conflict": "所选日期内房间床位不足",
    "housing_gender_conflict": "所选日期内存在性别冲突",
    "housing_person_time_conflict": "该人员已有重叠的入住记录",
    "minor_guardian_required": "未成年人缺少有效监护资料，请使用手动建档补齐",
    "housing_profile_incomplete": "入住资料不完整，请使用手动建档核实",
}


def _error(detail: str, *, status: int = 409, code: str = "quick_intake_conflict") -> ProblemError:
    return ProblemError(status=status, code=code, detail=detail)


def _at(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=BUSINESS_TIME_ZONE)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


class QuickIntakeService:
    def __init__(self, approval: PersonnelApprovalService) -> None:
        self.approval = approval
        self.db: Session = approval.db
        self.cipher = approval.cipher
        self.lookup = approval.lookup
        self.housing = HousingOccupancyService(self.db, resident_readiness=HousingResidentReadinessPolicy(
            self.db, cipher=self.cipher, lookup=self.lookup))

    def _room_authority(self, actor: AuthContext, room: HousingRoom) -> None:
        try:
            _authorize_locked_room(self.db, actor, datetime.now(UTC), room, "housing.stays.manage")
        except LockedHousingAuthorizationDenied:
            raise _error("房间不存在或没有安排入住权限", status=403) from None

    def _room_ready(self, room: HousingRoom, expected_type: str | None) -> None:
        if expected_type and room.room_type != expected_type:
            raise _error("表格房型与系统房型不一致，请先核实房间配置")
        self.housing._require_allocatable_room(room, allow_pending_verification=False)

    def _resolve_room(self, actor: AuthContext, code: str, now: datetime) -> HousingRoom:
        candidates = list(self.db.scalars(select(HousingRoom).join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id).where(
            (HousingRoom.normalized_key == code)
            | (func.upper(HousingBuilding.code + "-" + HousingRoom.room_number) == code)
        )))
        access = HousingAccessService(self.db, actor.account.id, now)
        visible = [room for room in candidates if access.can_read_room(room) and access.can_manage_stays(room)]
        if len(visible) != 1:
            raise _error("房号未唯一匹配到有权限的房间，请核对楼栋及完整房号")
        return visible[0]

    def _team(self, row: ParsedImportRow, result: PreviewRow, identity: QuickIdentity,
              teams: list[StaffTeamOption], choice: UUID | str | None) -> UUID | None:
        if identity not in {"team_manager", "coach", "assistant_coach", "team_member"}:
            result.warnings.append("项目/岗位说明将保存为住宿备注；项目和教练归属可在档案中继续完善")
            return None
        job = _compact(row.job_text)
        exact = [team for team in teams if _compact(team.name) in job]
        matches = exact or [team for team in teams if _compact(team.project_name) in job]
        result.team_options = matches
        result.project_label = "、".join(sorted({team.project_name for team in matches}))
        if choice == "pending":
            result.warnings.append("队伍待划分；原岗位说明保存为住宿备注")
            return None
        if choice is not None:
            if choice not in {team.id for team in matches}:
                result.errors.append("所选队伍已不在可选范围，请重新选择或选择待划分")
                return None
            return cast(UUID, choice)
        if len(matches) > 1:
            result.needs_team_choice = True
            result.errors.append("匹配到多个队伍，请选择队伍或明确选择待划分")
            return None
        if matches:
            return matches[0].id
        result.warnings.append("未匹配到唯一队伍，按待划分建档；原岗位说明保存为住宿备注")
        return None

    def preview(self, actor: AuthContext, input: PreviewInput, *, now: datetime | None = None) -> PreviewResult:
        require_direct_intake(self.db, actor)
        _require_current_care_actor(self.db, actor)
        now = now or datetime.now(UTC)
        import_date = input.default_arrival_date or business_date_at(now)
        try:
            parsed = parse_table(input.text.get_secret_value(), import_date=import_date)
        except ValueError as exc:
            raise _error(str(exc), status=422) from None
        counts = Counter(row.identity_number for row in parsed if row.identity_number)
        teams = PersonStaffQuery(self.db, lookup=self.lookup).staff_team_options(actor, for_intake=True).items
        results = []
        for row in parsed:
            result = PreviewRow(row_number=row.row_number, full_name=row.full_name[:80],
                room_label=row.room_code[:64], arrival_date=row.arrival_date, departure_date=row.departure_date,
                masked_identity=(row.identity_number[:3] + "***********" + row.identity_number[-4:]) if len(row.identity_number) == 18 else "格式待核实",
                masked_phone=(row.phone[:3] + "****" + row.phone[-4:]) if re.fullmatch(r"1[0-9]{10}", row.phone) else "格式待核实",
                errors=list(row.errors))
            results.append(result)
            if row.errors:
                # A failed split has no trustworthy fields for business validation.
                # Keep its actionable parse error instead of reporting empty names.
                continue
            if counts[row.identity_number] > 1:
                result.errors.append("同批次身份证重复，请保留核实后的唯一记录")
            try:
                if not 1 <= len(row.full_name) <= 80 or not 1 <= len(row.job_text) <= 120:
                    raise _error("姓名或岗位文字长度不符合要求")
                if any(key in row.job_text for key in ("管理员", "超管", "财务")):
                    raise _error("一键导入不支持管理员、超级管理员或财务岗位")
                matches = {(label, key) for label, key in _IDENTITIES if label in row.job_text}
                if re.search(r"(?:^|[\s/、，,]|兼)经理(?=$|[\s/、，,]|兼)", row.job_text):
                    matches.add(("队伍经理", "team_manager"))
                identities = {key for _, key in matches}
                if len(identities) != 1:
                    raise _error("岗位未唯一识别，请明确填写学员、队员、教练、助教等岗位")
                identity = next(iter(identities))
                result.identity_label = next(label for label, key in _IDENTITIES if key == identity)
                document = normalize_cn_id(row.identity_number)
                phone = normalize_phone(row.phone)
                result.birth_date, result.sex = document.birth_date, document.sex
                if is_minor(document.birth_date, business_date_at(now)) or (row.arrival_date and is_minor(document.birth_date, row.arrival_date)):
                    raise _error("未成年人须补齐监护资料，请使用手动建档后安排入住")
                if row.arrival_date is None:
                    raise _error("入住日期无效")
                if row.departure_date and (row.departure_date <= row.arrival_date or _at(row.departure_date) <= now):
                    raise _error("离开日期必须晚于入住日期且尚未到期；历史住宿请人工核实")
                if self.db.scalar(select(Person.id).where(Person.identity_digest == self.lookup.digest(document.number))) is not None:
                    raise _error("该证件已有档案，请在人员中心核对并安排入住；本次不会修改已有人员")
                room = self._resolve_room(actor, row.room_code, now)
                self._room_ready(room, row.room_type)
                stays = list(self.db.scalars(select(HousingStay).where(HousingStay.room_id == room.id,
                    HousingStay.status.in_(["planned", "checked_in", "checkout_pending", "checked_out"]), effective_stay_clause())))
                self.housing._require_no_stay_conflicts(person=Person(id=uuid4(), sex=document.sex), room=room,
                    target_start=_at(row.arrival_date), target_end=_at(row.departure_date) if row.departure_date else None,
                    reference_at=now, active_stays=stays)
                result.selected_team_id = self._team(row, result, identity, teams, input.team_choices.get(row.row_number))
                if result.errors:
                    continue
                ticket = ImportTicket(actor_id=actor.account.id, issued_at=now, expires_at=now + timedelta(minutes=15),
                    room_id=room.id, room_type=room.room_type, full_name=row.full_name, identity_number=document.number,
                    phone=phone, birth_date=document.birth_date, sex=document.sex, arrival_date=row.arrival_date,
                    departure_date=row.departure_date, identity=identity, job_text=row.job_text, team_id=result.selected_team_id)
                result.ticket = self.cipher.encrypt(ticket.model_dump_json(), field_name=_AAD)
            except IdentityValidationError as exc:
                result.errors.append(str(exc))
            except ProblemError as exc:
                result.errors.append(exc.detail)
            except HousingServiceError as exc:
                result.errors.append(_HOUSING_ERRORS.get(exc.code, "房态条件不符合要求，请在房态系统核实"))
        return PreviewResult(import_date=import_date, rows=results)

    def commit(self, actor: AuthContext, input: CommitInput, request: Request) -> CommitResult:
        require_direct_intake(self.db, actor)
        raw = input.ticket.get_secret_value()
        try:
            ticket = ImportTicket.model_validate_json(self.cipher.decrypt(raw, field_name=_AAD))
        except (EncryptedFieldError, ValidationError):
            raise _error("预览凭据无效，请重新识别", status=422) from None
        if ticket.actor_id != actor.account.id:
            raise _error("预览凭据不属于当前账号，请重新识别", status=403)
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        for attempt in range(3):
            try:
                with atomic_write_transaction(self.db):
                    result = self._commit_row(actor, ticket, input.idempotency_key, fingerprint, request)
                return result
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) not in {"40P01", "40001"}:
                    raise
                # The outer atomic unit has rolled back. Never retry just a housing savepoint.
                self.db.expire_all()
                if attempt == 2:
                    raise _error("并发操作较多，该行未导入，请保留原预览重试", status=503) from None
            except LockedHousingAuthorizationDenied:
                raise _error("当前账号已无房间操作权限，请重新识别", status=403) from None
            except HousingServiceError as exc:
                raise _error(_HOUSING_ERRORS.get(exc.code, "入住条件发生变化，该行未导入，请核实后重试")) from None
        raise AssertionError("unreachable")

    def _commit_row(self, actor: AuthContext, ticket: ImportTicket, key: UUID,
                    fingerprint: str, request: Request) -> CommitResult:
        lock_key = int.from_bytes(hashlib.sha256(f"quick-intake:{actor.account.id}:{key}".encode()).digest()[:8], "big", signed=True)
        self.db.execute(select(func.pg_advisory_xact_lock(lock_key)))
        require_direct_intake(self.db, actor)
        _require_current_care_actor(self.db, actor)
        room = self.db.get(HousingRoom, ticket.room_id, populate_existing=True)
        if room is None:
            raise _error("房间已不存在，请重新识别")
        self._room_authority(actor, room)
        previous = self.db.get(QuickIntakeReceipt, (actor.account.id, key))
        if previous:
            # Replay has no credential/relationship work. New writes must use
            # approval_service's complete sorted lock graph, never prelock actor.
            self.db.execute(select(Person.id).where(Person.id == actor.person.id).with_for_update()).all()
            self.db.execute(select(UserAccount.id).where(UserAccount.id == actor.account.id).with_for_update()).all()
            require_direct_intake(self.db, actor)
            self._room_authority(actor, room)
            if previous.fingerprint != fingerprint:
                raise _error("同一操作标识对应不同预览，请核对已成功的导入记录", code="quick_intake_key_conflict")
            return CommitResult(person_id=previous.person_id, stay_id=previous.stay_id,
                stay_status=cast("LiteralStayStatus", previous.stay_status), role_pending=previous.role_pending)
        now = datetime.now(UTC)
        if ticket.expires_at <= now:
            raise _error("预览已超过 15 分钟，请重新识别", code="quick_intake_expired")
        if ticket.departure_date and _at(ticket.departure_date) <= now:
            raise _error("计划退宿日已到，请重新核实日期")
        selection = ManagedIdentityInput(primary_identity=ticket.identity)
        resolved = resolve_managed_identity(selection)
        groups = []
        if ticket.team_id and ticket.identity in {"team_manager", "coach", "assistant_coach"}:
            role: StaffRole = "manager" if ticket.identity == "team_manager" else cast(StaffRole, ticket.identity)
            groups = [StaffTeamSelection(staff_role=role, team_ids=[ticket.team_id])]
        # The manual editor accepts public UUIDs too. Domain-separated keyed IDs
        # prevent its successful operation from masquerading as this row's create.
        approval_key = UUID(hex=self.lookup.digest_domain(
            "quick-intake-approval-operation", f"{actor.account.id}:{key}")[:32])
        approval = self.approval.direct_create_pending(actor, PendingIntakeInput(
            full_name=ticket.full_name, identity_number=SecretStr(ticket.identity_number), phone=SecretStr(ticket.phone),
            sex=ticket.sex, date_of_birth=ticket.birth_date, kind=resolved.kind,
            team_id=ticket.team_id if ticket.identity == "team_member" else None,
            team_allocation_status="pending"), approval_key, identity_selection=selection, staff_teams=groups)

        def authorize(locked_room: HousingRoom) -> None:
            require_direct_intake(self.db, actor)
            self._room_authority(actor, locked_room)
            self._room_ready(locked_room, ticket.room_type)

        stay = self.housing.create_stay(ticket.room_id, StayCreate(person_id=approval.person_id,
            stay_purpose="permanent", duration_mode="fixed_term" if ticket.departure_date else "long_term",
            planned_check_in_at=_at(ticket.arrival_date), planned_check_out_at=_at(ticket.departure_date) if ticket.departure_date else None,
            notes=f"表格导入岗位/项目：{ticket.job_text}"), actor_user_id=actor.account.id,
            occurred_at=now, authorize_room=authorize)
        _audit(self.db, request, actor, action="housing.stay.create", resource_type="housing_stay",
            resource_id=stay.id, outcome="allowed", metadata={"room_id": str(room.id), "resident_person_id": str(approval.person_id), "stay_id": str(stay.id)})
        pending = self.db.scalar(select(IntakeRoleIntent.id).where(IntakeRoleIntent.person_id == approval.person_id)) is not None
        self.db.add(QuickIntakeReceipt(actor_id=actor.account.id, operation_key=key, fingerprint=fingerprint,
            room_id=room.id, person_id=approval.person_id, stay_id=stay.id, stay_status=stay.status, role_pending=pending))
        self.db.flush()
        return CommitResult(person_id=approval.person_id, stay_id=stay.id,
            stay_status=cast("LiteralStayStatus", stay.status), role_pending=pending)


LiteralStayStatus = Literal["planned", "checked_in"]

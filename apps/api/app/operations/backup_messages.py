from __future__ import annotations

# These names are a fixed allowlist shared in meaning with the host backup tool.
# Unknown codes/stages are never echoed: status files must not expose raw errors.
BACKUP_ERROR_REASONS: dict[str, str] = {
    "backup_failed": "最近一次备份或验证失败，请检查服务器备份日志。",
    "backup_unexpected_error": "备份遇到未预期的异常，请检查服务器备份日志。",
    "backup_preflight_failed": "备份前置检查失败，请检查工具、目录权限和运行环境。",
    "command_failed": "备份命令执行失败，请检查对应阶段的服务器备份日志。",
    "runtime_image_archive_failed": "导出运行镜像失败，请检查Docker服务、镜像状态和可用磁盘空间。",
    "runtime_image_load_failed": "恢复运行镜像失败，请检查镜像归档和Docker服务。",
    "runtime_inspection_failed": "无法读取运行容器或镜像信息，请检查Docker服务和容器状态。",
    "mounted_api_secrets_copy_failed": "复制接口服务恢复密钥失败，请检查密钥挂载和读取权限。",
    "mounted_database_secrets_copy_failed": "复制数据库恢复密钥失败，请检查密钥挂载和读取权限。",
    "tls_certificate_copy_failed": "复制HTTPS证书资料失败，请检查证书目录挂载和读取权限。",
    "container_files_copy_failed": "复制容器文件失败，请检查来源目录、读取权限和磁盘空间。",
    "container_read_failed": "读取运行容器数据失败，请检查容器状态和访问权限。",
    "database_dump_failed": "数据库备份导出失败，请检查数据库连接、读取权限和磁盘空间。",
    "database_metadata_failed": "读取数据库结构或容量失败，请检查数据库连接和读取权限。",
    "archive_encryption_failed": "备份归档加密失败，请检查加密工具、恢复密钥和磁盘空间。",
    "archive_decryption_failed": "备份归档解密失败，请检查恢复密钥是否匹配及归档是否完整。",
    "archive_authentication_failed": "备份完整性或签名校验失败，归档可能损坏，请保留该备份并检查独立副本。",
    "archive_identity_mismatch": "备份编号与恢复资料不一致，请检查备份完整性。",
    "archive_members_invalid": "备份归档包含不允许的路径或文件类型，已停止恢复。",
    "backup_special_file_rejected": "备份目录包含不支持的链接或特殊文件，请检查备份范围。",
    "encryption_key_location_invalid": "恢复密钥存放位置不符合要求，请将密钥与备份目录分开保存。",
    "encryption_key_permissions_invalid": "恢复密钥文件权限不符合要求，请限制为备份执行账号可读写。",
    "encryption_key_invalid": "恢复密钥格式不正确，请检查密钥文件。",
    "encryption_key_unavailable": "无法读取恢复密钥，请检查文件是否存在及读取权限。",
    "encryption_key_already_exists": "恢复密钥已经存在，已阻止覆盖现有密钥。",
    "active_release_invalid": "无法确定当前ERP发布版本，请检查活动版本记录和恢复配置。",
    "backup_or_deployment_busy": "已有备份或部署任务正在执行，本次任务未取得执行锁。",
    "insufficient_disk_space": "备份磁盘剩余空间不足，无法安全完成备份和隔离恢复验证。",
    "snapshot_timeout": "等待数据库一致性快照超时，请检查数据库负载和连接。",
    "snapshot_invalid": "数据库快照信息无效，已停止备份，请检查数据库导出任务。",
    "snapshot_failed": "创建数据库一致性快照失败，请检查数据库连接和导出任务。",
    "snapshot_controller_closed": "数据库快照控制连接提前关闭，已停止备份，请检查数据库连接。",
    "media_root_invalid": "评价文件存储路径不符合备份要求，请检查文件目录配置。",
    "media_reference_invalid": "评价文件引用路径无效，请检查文件记录与存储目录。",
    "media_file_missing": "数据库引用的评价文件缺失，当前备份无法保证完整恢复。",
    "media_hash_mismatch": "评价文件校验值不一致，文件可能损坏或在备份时发生变化。",
    "recovery_file_checksum_mismatch": "恢复文件校验值不一致，请检查归档完整性和独立副本。",
    "recovery_file_missing": "备份缺少必要的恢复文件，当前备份无法完成恢复验证。",
    "recovery_secret_mismatch": "恢复密钥与运行配置不一致，无法保证加密业务数据可恢复。",
    "recovery_payload_invalid": "恢复资料格式不正确，请检查备份内容和工具版本。",
    "restore_database_start_failed": "隔离验证数据库启动失败，请检查Docker服务、数据库镜像和可用资源。",
    "restore_database_unavailable": "隔离验证数据库未能就绪，请检查数据库启动日志和可用资源。",
    "restore_database_failed": "向隔离数据库还原备份失败，请检查备份文件和数据库兼容性。",
    "restore_verification_failed": "隔离恢复验证程序执行失败，请检查验证容器和备份日志。",
    "backup_container_check_failed": "恢复验证容器检查失败，请检查验证环境和服务器备份日志。",
    "restore_row_counts_mismatch": "恢复后的数据行数与备份快照不一致，当前备份尚未通过验证。",
    "restore_alembic_revision_mismatch": "恢复后的数据库迁移版本与备份记录不一致。",
    "restore_schema_columns_mismatch": "恢复后的数据库字段结构与备份记录不一致。",
    "restore_schema_constraints_mismatch": "恢复后的数据库约束与备份记录不一致。",
    "restore_schema_indexes_mismatch": "恢复后的数据库索引与备份记录不一致。",
    "restore_schema_sequences_mismatch": "恢复后的数据库序列与备份记录不一致。",
    "restore_sequence_evidence_invalid": "备份中的序列位置证据缺失或格式无效，无法确认编号可安全恢复。",
    "restore_sequence_state_mismatch": "恢复后的序列位置或调用状态与数据库备份不一致，已停止恢复验证。",
    "restore_sequence_next_identifier_unsafe": "恢复后的下一编号可能重复或序列已耗尽，当前备份尚未通过验证。",
    "field_key_canary_failed": "业务字段加密密钥验证失败，无法确认加密业务数据可恢复。",
    "lookup_key_canary_failed": "检索密钥验证失败，无法确认恢复后可正常查找账号和人员。",
    "restored_identity_decryption_failed": "恢复后的身份证资料无法解密，请检查备份中的业务加密密钥。",
    "restored_phone_decryption_failed": "恢复后的手机号资料无法解密，请检查备份中的业务加密密钥。",
    "restored_email_decryption_failed": "恢复后的邮箱资料无法解密，请检查备份中的业务加密密钥。",
    "restored_mail_username_decryption_failed": "恢复后的邮件服务账号无法解密，请检查备份中的业务加密密钥。",
    "restored_mail_password_decryption_failed": "恢复后的邮件服务密码无法解密，请检查备份中的业务加密密钥。",
    "restored_mail_sender_decryption_failed": "恢复后的发件邮箱配置无法解密，请检查备份中的业务加密密钥。",
    "restored_mail_reply_to_decryption_failed": "恢复后的邮件回复地址无法解密，请检查备份中的业务加密密钥。",
    "restore_cleanup_failed": "清理隔离恢复验证容器失败，请检查并清理本次验证资源。",
    "restore_cleanup_ownership_mismatch": "隔离验证容器的归属标识不一致，已停止自动清理以避免误删。",
    "postgres_major_version_mismatch": "恢复数据库的主版本与备份不一致，请使用匹配的PostgreSQL版本。",
    "secondary_destination_unavailable": "独立备份目标目录不可用，请检查备份设备是否已挂载。",
    "secondary_destination_not_independent": "副本目标与主备份位于同一文件系统，请选择独立存储设备。",
    "secondary_copy_busy": "存在尚未完成的副本复制任务，请检查目标设备和复制状态。",
    "requested_backup_unavailable": "指定的备份不存在或不完整，请选择有效的已完成备份。",
    "no_complete_backup": "尚无完整备份可供恢复验证，请先完成一次备份。",
    "recovery_target_must_be_new_directory": "恢复导出目标必须是尚不存在的新目录，以免覆盖现有数据。",
    "recovery_target_must_be_outside_backups": "恢复导出目录必须位于备份目录之外。",
}

BACKUP_STAGE_LABELS: dict[str, str] = {
    "preflight": "备份前置检查",
    "copy_recovery_configuration": "复制恢复配置和密钥",
    "capture_database_snapshot": "导出数据库一致性快照",
    "save_runtime_images": "保存运行镜像",
    "copy_evaluation_media": "复制评价文件",
    "copy_evaluation_quarantine": "复制评价隔离区文件",
    "compress_backup_archive": "压缩备份归档",
    "encrypt_backup_archive": "加密备份归档",
    "authenticate_encrypted_archive": "校验加密备份完整性",
    "unpack_recovery_archive": "解密并展开恢复资料",
    "verify_recovery_files": "核对恢复文件",
    "start_isolated_database": "启动隔离验证数据库",
    "restore_isolated_database": "还原隔离验证数据库",
    "verify_restored_database": "验证恢复后的数据库和加密资料",
    "complete": "备份与恢复验证完成",
}


def backup_message(status: str, error: object, stage: object) -> tuple[str | None, str]:
    label = BACKUP_STAGE_LABELS.get(stage) if isinstance(stage, str) else None
    if status == "ok":
        return None, "最近一次备份与隔离恢复验证成功"
    if status == "running":
        return None, f"正在{label}" if label else "备份正在执行"
    code = error if isinstance(error, str) and error in BACKUP_ERROR_REASONS else "backup_failed"
    reason = BACKUP_ERROR_REASONS[code]
    if label and stage != "complete":
        reason = f"在“{label}”阶段失败：{reason}"
    return code, reason

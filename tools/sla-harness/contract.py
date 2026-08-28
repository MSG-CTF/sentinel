"""Sentinel 외부 계약의 단일 기준.

플랫폼 공통 규약을 따른다. 모든 키는 snake_case, 식별자는 UUID 문자열,
시각은 ISO 8601 UTC Z. Sentinel 구현이 어떤 언어든 이 스키마를 지키면
하네스를 통과한다.
"""

import re
import uuid

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

TARGET_TYPES = {"BACKEND", "KOTH", "INSTANCE", "SCHEDULER", "BROKER"}
HEALTH_STATUSES = {"HEALTHY", "DEGRADED", "DOWN"}
SIGNAL_ACTIONS = {"RESTART", "CLEANUP", "RESCHEDULE", "INVESTIGATE"}
ALERT_SEVERITIES = {"INFO", "WARNING", "CRITICAL"}

# 스케줄러로 보내는 자동 처리 신호
HEALTH_SIGNAL_FIELDS = {
    "signal_id": "uuid",
    "target_id": "string",
    "target_type": TARGET_TYPES,
    "status": HEALTH_STATUSES,
    "action": SIGNAL_ACTIONS,
    "reason": "string",
    "checked_at": "iso_utc",
}

# 운영자에게 보내는 알림
ADMIN_ALERT_FIELDS = {
    "alert_id": "uuid",
    "severity": ALERT_SEVERITIES,
    "title": "string",
    "message": "string",
    "source": "string",
    "created_at": "iso_utc",
}


def _walk_keys(value, path, violations):
    if isinstance(value, dict):
        for key, child in value.items():
            if not SNAKE.fullmatch(str(key)):
                violations.append(f"{path}.{key}: snake_case 위반")
            _walk_keys(child, f"{path}.{key}", violations)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_keys(child, f"{path}[{index}]", violations)


def validate_payload(kind, payload):
    """계약 위반 목록을 돌려준다. 비어 있으면 통과다."""
    schema = {"health_signal": HEALTH_SIGNAL_FIELDS, "admin_alert": ADMIN_ALERT_FIELDS}[kind]
    violations = []

    if not isinstance(payload, dict):
        return [f"{kind}: 객체가 아님"]

    _walk_keys(payload, kind, violations)

    for field, rule in schema.items():
        if field not in payload:
            violations.append(f"{kind}.{field}: 필수 필드 누락")
            continue
        value = payload[field]
        if rule == "uuid":
            try:
                uuid.UUID(str(value))
            except (ValueError, AttributeError, TypeError):
                violations.append(f"{kind}.{field}: UUID 문자열이 아님")
        elif rule == "iso_utc":
            if not isinstance(value, str) or not ISO_UTC.fullmatch(value):
                violations.append(f"{kind}.{field}: ISO UTC Z 형식이 아님")
        elif rule == "string":
            if not isinstance(value, str) or not value.strip():
                violations.append(f"{kind}.{field}: 비어 있지 않은 문자열이어야 함")
        elif isinstance(rule, set):
            if value not in rule:
                violations.append(f"{kind}.{field}: 허용값 밖 ({value})")

    return violations

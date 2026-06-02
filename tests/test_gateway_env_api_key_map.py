from __future__ import annotations

from gateway_core.infra.api_keys import current_api_key_record, set_current_api_key_record
from gateway_core.runtime.gateway_config import _require_gateway_auth


def test_env_gateway_key_school_map_sets_current_school_record(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "1")
    monkeypatch.setenv("GATEWAY_API_KEY_DB_ENABLED", "0")
    monkeypatch.setenv("GATEWAY_API_KEYS", "key_a,key_b")
    monkeypatch.setenv("GATEWAY_KEY_SCHOOL_MAP", "key_a=美兰湖中学;key_b=第二轻工业学校")
    set_current_api_key_record(None)

    token = _require_gateway_auth("Bearer key_a")
    record = current_api_key_record()

    assert token == "key_a"
    assert record is not None
    assert record.key_type == "school"
    assert record.school_id == "sch_zx_mlh"
    assert record.schema_name == "mlh"
    assert record.display_name == "美兰湖中学"

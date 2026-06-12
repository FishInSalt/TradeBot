def test_schemas_importable():
    from src.webui import schemas
    s = schemas.LiveStatus(status="active", last_active_at=None, position=None,
                           open_orders=[], active_alerts=[])
    assert s.model_dump()["status"] == "active"

"""Iter 3 migration tests — covers three-state sentinel + batch_alter + backfill.

Spec §5.2.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def alembic_cfg_factory(monkeypatch):
    """Factory that builds Alembic Config + sets TRADEBOT_DB_URL via monkeypatch (auto-cleanup).

    Test isolation: monkeypatch reverts env var after each test; safe under pytest-xdist concurrency.
    """
    def _factory(db_path: Path) -> Config:
        monkeypatch.setenv("TRADEBOT_DB_URL", f"sqlite+aiosqlite:///{db_path}")
        repo_root = Path(__file__).resolve().parents[1]
        return Config(str(repo_root / "alembic.ini"))
    return _factory


def _create_pre_alembic_schema(db_path: Path) -> None:
    """Hand-write FULL W1 schema for migration testing (path 2 fixture).

    Builds all 8 W1 business tables (matches spec §4.1 BEFORE Iter 3 changes).

    Tables migration upgrade() references directly:
    - sim_orders + ix_sim_orders_session_status (Step 1 drops this index)
    - tool_calls + ix_tool_calls_session_tool_time + ix_tool_calls_cycle (Step 1 drops these)
    - decision_logs + ix_decision_logs_session_id (Step 4 batch_alter rebuilds this table)
    - trade_actions (Step 3 add column)
    - sessions (FK target for all above)

    Tables NOT touched by this migration but included for completeness (= simulate W1
    production accurately, future migrations may touch these e.g. C档 drop market_summary):
    - memory_entries (FK to sessions)
    - sim_balances (PK = session_id, FK to sessions)
    - sim_positions (UNIQUE(session_id, symbol), FK to sessions)
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE sessions (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            symbol VARCHAR(50) NOT NULL,
            persona_config TEXT,
            model_config TEXT,
            initial_balance FLOAT NOT NULL,
            status VARCHAR(20) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            exchange_type VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            scheduler_interval_min INTEGER NOT NULL,
            approval_enabled BOOLEAN NOT NULL,
            alert_config TEXT,
            fee_rate FLOAT,
            token_budget INTEGER NOT NULL,
            last_active_at DATETIME
        );
        CREATE TABLE decision_logs (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            cycle_id VARCHAR(50) NOT NULL,
            trigger_type VARCHAR(20) NOT NULL,
            market_summary TEXT,
            decision VARCHAR(50) NOT NULL,
            reasoning TEXT,
            model_used VARCHAR(100),
            tokens_used INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_decision_logs_session_id ON decision_logs (session_id);
        CREATE TABLE trade_actions (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            action VARCHAR(30) NOT NULL,
            order_id VARCHAR(36),
            symbol VARCHAR(50) NOT NULL,
            side VARCHAR(10),
            trigger_reason VARCHAR(20),
            price FLOAT,
            pnl FLOAT,
            reasoning TEXT,
            fee FLOAT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_trade_actions_session_id ON trade_actions (session_id);
        CREATE TABLE tool_calls (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            cycle_id VARCHAR(50) NOT NULL,
            tool_name VARCHAR(60) NOT NULL,
            status VARCHAR(10) NOT NULL,
            duration_ms INTEGER NOT NULL,
            error_type VARCHAR(100),
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_tool_calls_session_tool_time ON tool_calls (session_id, tool_name, created_at);
        CREATE INDEX ix_tool_calls_cycle ON tool_calls (cycle_id);
        CREATE TABLE sim_orders (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            order_id VARCHAR(36) NOT NULL UNIQUE,
            symbol VARCHAR(50) NOT NULL,
            side VARCHAR(10) NOT NULL,
            position_side VARCHAR(10) NOT NULL,
            order_type VARCHAR(20) NOT NULL,
            amount FLOAT NOT NULL,
            trigger_price FLOAT,
            status VARCHAR(20) NOT NULL,
            filled_price FLOAT,
            fee FLOAT,
            filled_at DATETIME,
            created_at DATETIME NOT NULL,
            frozen_margin FLOAT NOT NULL DEFAULT 0.0,
            leverage INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_sim_orders_session_status ON sim_orders (session_id, status);
        CREATE TABLE memory_entries (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            memory_type VARCHAR(20) NOT NULL,
            category VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            relevance_score FLOAT NOT NULL,
            created_at DATETIME NOT NULL,
            expires_at DATETIME,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_memory_entries_session_id ON memory_entries (session_id);
        CREATE TABLE sim_balances (
            session_id VARCHAR(36) NOT NULL PRIMARY KEY,
            free_usdt FLOAT NOT NULL,
            used_usdt FLOAT NOT NULL,
            frozen_usdt FLOAT NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE TABLE sim_positions (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            symbol VARCHAR(50) NOT NULL,
            side VARCHAR(10) NOT NULL,
            contracts FLOAT NOT NULL,
            entry_price FLOAT NOT NULL,
            leverage INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE (session_id, symbol),
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_sim_positions_session_id ON sim_positions (session_id);
    """)
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_init_db_path_3_for_empty_db(tmp_path: Path) -> None:
    """Path 3 (empty DB → create_all + stamp head): NO migration upgrade run.

    Critical: migration upgrade() in empty DB hits "no such index" — first migration is
    ALTER not CREATE. Empty DB production path is init_db path 3.

    Asserts:
    1. Schema bootstrapped via Base.metadata.create_all (args / cycle_id / status / new index)
    2. alembic_version table stamped to head (sentinel #1 for next init_db call)
    """
    from src.storage.database import init_db

    db_path = tmp_path / "empty.db"
    await init_db(f"sqlite+aiosqlite:///{db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Schema completeness
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "status" in cols, "status column missing"
    assert cols["status"][3] == 1, "status should be NOT NULL"
    assert cols["decision"][2] == "VARCHAR(30)", f"decision should be VARCHAR(30), got {cols['decision'][2]}"
    assert "args" in {r[1] for r in cur.execute("PRAGMA table_info(tool_calls)")}
    assert "cycle_id" in {r[1] for r in cur.execute("PRAGMA table_info(trade_actions)")}
    indexes = {r[1] for r in cur.execute("SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='decision_logs'")}
    assert "ix_decision_logs_session_id_cycle_id" in indexes

    # 2. alembic_version stamped to head
    av = list(cur.execute("SELECT version_num FROM alembic_version"))
    assert len(av) == 1, f"alembic_version should have exactly 1 row, got {len(av)}"
    conn.close()


def test_upgrade_from_w1_like_data(tmp_path: Path, alembic_cfg_factory) -> None:
    """Path 2: pre-Alembic legacy DB with mock rows → batch_alter + backfill.

    Fixture builds FULL W1 schema (incl sim_orders) so migration Step 1 drop_index has target.
    Mock data: 4 rows decision='completed' + 1 row decision='usage_limit_exceeded'.
    Asserts:
    1. Migration does not raise (covers batch_alter merge semantics + INSERT SELECT NOT NULL path)
    2. All 5 rows have decision='legacy'
    3. 4 rows status='ok' (from server_default) + 1 row status='usage_limit_exceeded' (catch-net)
    """
    db_path = tmp_path / "w1_like.db"
    _create_pre_alembic_schema(db_path)

    # Insert 5 rows (4 completed + 1 usage_limit_exceeded)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (id, name, symbol, initial_balance, status, created_at, updated_at,
                              exchange_type, timeframe, scheduler_interval_min, approval_enabled,
                              token_budget)
        VALUES ('s1', 'test', 'BTC/USDT:USDT', 100.0, 'active',
                '2026-04-27T00:00:00+00:00', '2026-04-27T00:00:00+00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    for i, dec in enumerate(["completed", "completed", "completed", "completed", "usage_limit_exceeded"]):
        cur.execute("""
            INSERT INTO decision_logs (session_id, cycle_id, trigger_type, decision, tokens_used, created_at)
            VALUES ('s1', ?, 'scheduled', ?, 0, '2026-04-27T00:00:00+00:00')
        """, (f"cyc-{i}", dec))
    conn.commit()
    conn.close()

    # Run migration (must not raise)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    # Verify backfill
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    decisions = dict(cur.execute("SELECT decision, COUNT(*) FROM decision_logs GROUP BY decision"))
    assert decisions == {"legacy": 5}, f"expected all 5 rows decision='legacy', got {decisions}"
    statuses = dict(cur.execute("SELECT status, COUNT(*) FROM decision_logs GROUP BY status"))
    assert statuses == {"ok": 4, "usage_limit_exceeded": 1}, f"expected 4 ok + 1 usage_limit_exceeded, got {statuses}"

    # Verify id preservation (spec §4.2 batch_alter contract: INSERT INTO _new SELECT * FROM old preserves id sequence).
    # Last line of defense if future alembic versions change batch_alter semantics.
    ids = [r[0] for r in cur.execute("SELECT id FROM decision_logs ORDER BY id")]
    assert ids == [1, 2, 3, 4, 5], f"id sequence broken after batch_alter: expected [1,2,3,4,5], got {ids}"
    conn.close()


def test_downgrade_then_upgrade(tmp_path: Path, alembic_cfg_factory) -> None:
    """From W1-like fixture: upgrade head → downgrade -1 → upgrade head reentrant + idempotent."""
    db_path = tmp_path / "reentrant.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    # Verify final state has all new fields
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    assert "status" in {r[1] for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "args" in {r[1] for r in cur.execute("PRAGMA table_info(tool_calls)")}
    assert "cycle_id" in {r[1] for r in cur.execute("PRAGMA table_info(trade_actions)")}
    conn.close()


def test_upgrade_when_already_head(tmp_path: Path, alembic_cfg_factory) -> None:
    """Production critical path: alembic upgrade head when already at head is no-op + no error.

    Three-state sentinel #1 (alembic_version exists) runs upgrade head every init_db call.
    """
    db_path = tmp_path / "already_head.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # Second call should be no-op

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = list(cur.execute("SELECT version_num FROM alembic_version"))
    assert len(rows) == 1
    conn.close()


def test_r2_4_upgrade_widens_tool_calls_status(tmp_path: Path, alembic_cfg_factory):
    """R2-4: tool_calls.status String(10) → String(20)。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)  # Iter 3 migration 是 ALTER 不是 CREATE，必须先建 W1 schema
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(tool_calls)")}
    assert "status" in cols
    assert cols["status"][2] == "VARCHAR(20)", \
        f"tool_calls.status 期望 VARCHAR(20)，实际 {cols['status'][2]}"


def test_r2_4_upgrade_widens_decision_logs_decision(tmp_path: Path, alembic_cfg_factory):
    """R2-4: decision_logs.decision String(20) → String(30)。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "decision" in cols
    assert cols["decision"][2] == "VARCHAR(30)", \
        f"decision_logs.decision 期望 VARCHAR(30)，实际 {cols['decision'][2]}"


def test_r2_4_upgrade_preserves_historical_adjust_rows(tmp_path: Path, alembic_cfg_factory):
    """R2-4 不动 'adjust' 历史行（A 方案，spec §5.5）。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    # 跑到 Iter 3 head（不含 R2-4）
    command.upgrade(cfg, "379f62306805")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 模拟 Iter 4 之后写入的 adjust 行（W1 schema sessions 多列 NOT NULL，沿用 line 222 INSERT 模板）
    cur.execute("""
        INSERT INTO sessions (id, name, symbol, initial_balance, status, created_at, updated_at,
                              exchange_type, timeframe, scheduler_interval_min, approval_enabled,
                              token_budget)
        VALUES ('sess-x', 'pre-r2-4', 'BTC/USDT:USDT', 100.0, 'active',
                '2026-04-30T00:00:00+00:00', '2026-04-30T00:00:00+00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    cur.execute(
        "INSERT INTO decision_logs "
        "(session_id, cycle_id, trigger_type, decision, status, tokens_used, created_at) "
        "VALUES ('sess-x', 'cyc-x', 'scheduled', 'adjust', 'ok', 0, datetime('now'))"
    )
    conn.commit()
    conn.close()

    # 跑 R2-4 upgrade
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = list(cur.execute("SELECT decision FROM decision_logs WHERE cycle_id = 'cyc-x'"))
    assert len(rows) == 1, f"期望 1 行，实际 {len(rows)}"
    assert rows[0][0] == "adjust", \
        f"R2-4 不应 backfill 历史 'adjust' → 实际 {rows[0][0]!r}"


def test_r2_4_upgrade_preserves_existing_indexes(tmp_path: Path, alembic_cfg_factory):
    """R2-4 不动 Iter 3 已建索引。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    indexes = {
        r[1] for r in cur.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='decision_logs'"
        )
    }
    assert "ix_decision_logs_session_id_cycle_id" in indexes, \
        f"Iter 3 索引应保留，实际 indexes={indexes}"


# ===================== R2-7 §10.2 T-MIG-1~8: agent_cycle schema reframe =====================


def test_t_mig_1_table_renamed_to_agent_cycles(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-1 (AC1): decision_logs 表 → agent_cycles 表 rename。"""
    db_path = tmp_path / "t_mig_1.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "agent_cycles" in tables, f"agent_cycles 表应存在，实际 tables={tables}"
    assert "decision_logs" not in tables, f"decision_logs 表应已 rename，实际 tables={tables}"
    conn.close()


def test_t_mig_2_columns_renamed(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-2 (AC1): 5 列 rename + state_snapshot 加列 + 精确等值列集合。"""
    db_path = tmp_path / "t_mig_2.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    expected = {
        "id", "session_id", "cycle_id", "triggered_by", "trigger_context",
        "state_snapshot", "decision", "execution_status", "reasoning",
        "model_id", "tokens_consumed", "created_at",
    }
    assert cols == expected, f"agent_cycles 列集合不匹配，差异={cols ^ expected}"
    conn.close()


def test_t_mig_3_decision_text_nullable(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-3 (AC2): decision String(30) NOT NULL → Text NULLABLE。"""
    db_path = tmp_path / "t_mig_3.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    assert "decision" in cols
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    assert cols["decision"][2] == "TEXT", f"decision type 期望 TEXT，实际 {cols['decision'][2]!r}"
    assert cols["decision"][3] == 0, f"decision 应 NULLABLE (notnull=0)，实际 notnull={cols['decision'][3]}"
    conn.close()


def test_t_mig_4_state_snapshot_column_exists(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-4 (AC10): state_snapshot Text NULLABLE 列加成功。"""
    db_path = tmp_path / "t_mig_4.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    assert "state_snapshot" in cols, "state_snapshot 列应存在"
    assert cols["state_snapshot"][2] == "TEXT", \
        f"state_snapshot type 期望 TEXT，实际 {cols['state_snapshot'][2]!r}"
    assert cols["state_snapshot"][3] == 0, \
        f"state_snapshot 应 NULLABLE (notnull=0)，实际 notnull={cols['state_snapshot'][3]}"
    conn.close()


def test_t_mig_5_index_renamed(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-5: index ix_decision_logs_session_id_cycle_id → ix_agent_cycles_session_id_cycle_id。"""
    db_path = tmp_path / "t_mig_5.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    indexes = {
        r[1] for r in cur.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='agent_cycles'"
        )
    }
    assert "ix_agent_cycles_session_id_cycle_id" in indexes, \
        f"新索引应存在，实际 indexes={indexes}"
    assert "ix_decision_logs_session_id_cycle_id" not in indexes, \
        f"旧索引应已 drop，实际 indexes={indexes}"
    conn.close()


def test_t_mig_6_historical_data_compat(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-6 (AC1): R2-4 head 历史 decision_logs 数据 → R2-7 upgrade 后保留 + decision 字面量原样。

    两步 upgrade：先 R2-4 head 插数据（含 5 类 decision），再 R2-7 upgrade。
    """
    db_path = tmp_path / "t_mig_6.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    # 先升到 R2-4 head
    command.upgrade(cfg, "e7b2bd73c131")

    # 插历史数据（5 行覆盖 R2-4 子类型）
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (id, name, symbol, initial_balance, status, created_at, updated_at,
                              exchange_type, timeframe, scheduler_interval_min, approval_enabled,
                              token_budget)
        VALUES ('s_hist', 'hist', 'BTC/USDT:USDT', 100.0, 'active',
                '2026-04-30T00:00:00+00:00', '2026-04-30T00:00:00+00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    historical = [
        ("cyc-h1", "open_long"),
        ("cyc-h2", "close"),
        ("cyc-h3", "adjust_protect"),
        ("cyc-h4", "hold"),
        ("cyc-h5", "derive_error"),
    ]
    for cyc, dec in historical:
        cur.execute(
            "INSERT INTO decision_logs "
            "(session_id, cycle_id, trigger_type, decision, status, tokens_used, created_at) "
            "VALUES ('s_hist', ?, 'scheduled', ?, 'ok', 0, datetime('now'))",
            (cyc, dec),
        )
    conn.commit()
    conn.close()

    # 升到 R2-7 head
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = list(cur.execute(
        "SELECT cycle_id, decision FROM agent_cycles WHERE session_id='s_hist' ORDER BY cycle_id"
    ))
    assert len(rows) == 5, f"期望 5 行历史数据保留，实际 {len(rows)}"
    actual = dict(rows)
    expected = dict(historical)
    assert actual == expected, f"decision 字面量应保留原样，差异={set(actual.items()) ^ set(expected.items())}"
    conn.close()


def test_t_mig_7_downgrade_no_null_decision(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-7: downgrade 路径在无 NULL decision 行时应成功（escape hatch 注释场景）。"""
    db_path = tmp_path / "t_mig_7.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    # 先升到 R2-7 head
    command.upgrade(cfg, "head")

    # 插一行非 NULL decision（plan 设计：downgrade 必须在无 NULL 行下跑）
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (id, name, symbol, initial_balance, status, created_at, updated_at,
                              exchange_type, timeframe, scheduler_interval_min, approval_enabled,
                              token_budget)
        VALUES ('s_d', 'down', 'BTC/USDT:USDT', 100.0, 'active',
                '2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    cur.execute(
        "INSERT INTO agent_cycles "
        "(session_id, cycle_id, triggered_by, decision, execution_status, tokens_consumed, created_at) "
        "VALUES ('s_d', 'cyc-d1', 'scheduled', 'open_long', 'ok', 0, datetime('now'))"
    )
    conn.commit()
    conn.close()

    # downgrade -1 必须成功
    command.downgrade(cfg, "-1")

    # 验证：表名回滚，列名回滚
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "decision_logs" in tables and "agent_cycles" not in tables, \
        f"downgrade 后表名应回滚，实际 tables={tables}"
    cols = {r[1] for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "trigger_type" in cols and "triggered_by" not in cols, \
        f"downgrade 后列名应回滚，实际 cols={cols}"
    assert "state_snapshot" not in cols, "state_snapshot 应已 drop"
    conn.close()


def test_t_mig_8_execution_status_server_default_preserved(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-8 (M5/AC14): execution_status server_default='ok' rename 后保留。"""
    db_path = tmp_path / "t_mig_8.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    assert "execution_status" in cols
    # PRAGMA table_info 第 5 列 dflt_value 可能含/不含引号
    dflt = cols["execution_status"][4]
    assert dflt in ("'ok'", "ok"), \
        f"execution_status server_default 期望 'ok'，实际 {dflt!r}"
    conn.close()

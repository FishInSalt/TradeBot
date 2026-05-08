"""AC-12: forensic enum drift-guard — 防 R2-Next-J 等加新 forensic enum 后 view 漏判.

PR #42 三审 I-1 修订：原版 query 跑在空 db_session 上（agent_cycles 0 行）→
永远返 [] → 测试同义反复无防御价值。改造为 static-source check：
1. grep src/cli/app.py 实际写入的 execution_status 字面值
2. 对照 KNOWN_ENUMS whitelist（与 view CASE 列举对齐）
3. 验证 src/storage/views.py is_forensic_cycle CASE 覆盖所有 forensic enum

新加 forensic enum 时需同时改：cli/app.py + KNOWN_FORENSIC_ENUMS + views.py。
"""
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

# Whitelist：所有合法 execution_status 值。新加 forensic enum 时同步更新此 set
# AND src/storage/views.py 的 is_forensic_cycle CASE。
KNOWN_OK_ENUM = "ok"
KNOWN_FORENSIC_ENUMS = frozenset({"retry_exhausted", "usage_limit_exceeded"})
KNOWN_ENUMS = frozenset({KNOWN_OK_ENUM}) | KNOWN_FORENSIC_ENUMS


def test_cli_app_execution_status_writes_only_known_enums():
    """T21.1: src/cli/app.py 写入 execution_status 的字面值必须在 whitelist 内。

    grep 字面值 'execution_status="..."' 与 KNOWN_ENUMS 对比；新加 enum 需先扩
    KNOWN_ENUMS 才能通过。Dynamic write (`r.execution_status`) 不影响。
    """
    cli_app = (REPO_ROOT / "src" / "cli" / "app.py").read_text(encoding="utf-8")
    # Match: execution_status="<literal>" / execution_status='<literal>'
    literals = set(re.findall(r"""execution_status=["']([^"']+)["']""", cli_app))

    unknown = literals - KNOWN_ENUMS
    assert not unknown, (
        f"src/cli/app.py writes execution_status literal(s) not in whitelist: {unknown}. "
        f"Update KNOWN_ENUMS in this test, AND v_cycle_metrics.is_forensic_cycle CASE "
        f"in src/storage/views.py if the new enum is forensic."
    )


def test_view_is_forensic_cycle_lists_all_forensic_enums():
    """T21.2: views.py 的 is_forensic_cycle CASE 必须列举所有 forensic enum.

    若 cli/app.py 加新 forensic enum 但 view 漏更新，is_forensic_cycle 会返 0 让
    forensic cycle 错误地不被标记，污染下游 ok-cycle 比例统计。
    """
    views_py = (REPO_ROOT / "src" / "storage" / "views.py").read_text(encoding="utf-8")
    # Find the IN list whose CASE assigns is_forensic_cycle
    m = re.search(
        r"execution_status\s+IN\s+\(([^)]+)\)\s+THEN\s+1\s+ELSE\s+0\s+END\s+AS\s+is_forensic_cycle",
        views_py, re.DOTALL,
    )
    assert m, "Could not locate is_forensic_cycle CASE pattern in views.py"
    in_list = set(re.findall(r"'([^']+)'", m.group(1)))

    missing = KNOWN_FORENSIC_ENUMS - in_list
    extra = in_list - KNOWN_FORENSIC_ENUMS
    assert not missing, (
        f"views.py is_forensic_cycle CASE missing enum(s): {missing}. "
        f"Update v_cycle_metrics SQL to include them."
    )
    assert not extra, (
        f"views.py is_forensic_cycle CASE references unknown enum(s): {extra}. "
        f"Update KNOWN_FORENSIC_ENUMS in this test if intentional."
    )

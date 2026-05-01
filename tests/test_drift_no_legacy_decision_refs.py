"""R2-7 §14 G7: drift guard — 派生路线 + DecisionLog 残留扫描.

捕获未来 regression：派生符号 / 旧表名/类名 通过 merge / refactor / accidental
copy 偷偷回流 source. Whitelist 单一机制：path-anchored regex（避免 substring
match 对未来 `_v2.py` 类似名字 / 文件内容含测试名 silent whitelist 的风险）.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

DERIVE_ROUTE_SYMBOLS = [
    "_derive_decision_from_actions",
    "PROTECT_ACTIONS",
    "ENTRY_ORDER_ACTIONS",
    "LEVERAGE_ACTIONS",
    "ALERT_ACTIONS",
    "ADJUST_ACTIONS",
    "DERIVE_DECISION_VALUES",
]

LEGACY_NAMES = ["DecisionLog", "decision_logs"]


def _grep(pattern: str, paths: list[Path]) -> list[str]:
    """Grep -E pattern across paths, return list of "file:line: match".

    --include="*.py" 限定 Python 源文件，跳过 __pycache__ .pyc / .md / 其他 binary。
    """
    cmd = ["grep", "-rEn", "--include=*.py", pattern] + [str(p) for p in paths]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # returncode 0 = matches, 1 = no matches, 2+ = real error (PATH / perm / bad pattern)
    assert proc.returncode in (0, 1), f"grep failed (rc={proc.returncode}): {proc.stderr}"
    return proc.stdout.splitlines()


def test_g7_derive_route_symbols_no_residual():
    """G7-1 (R2-7 §14): 派生路线 7 个符号在 src/ + tests/ 内 0 hit (除 whitelist)。

    Whitelist (path-anchored regex):
    1. 本测试文件自身（含定义）
    2. tests/test_storage.py G1 SoT docstring 引 `ADJUST_ACTIONS drift guard` 作历史
       类比（不是 code residual）— 通过精准 line+pattern whitelist
    """
    paths = [REPO_ROOT / "src", REPO_ROOT / "tests"]
    pattern = r"\b(" + "|".join(DERIVE_ROUTE_SYMBOLS) + r")\b"
    hits = _grep(pattern, paths)

    WHITELIST_PATTERNS = [
        # 测试自身
        re.compile(r"tests/test_drift_no_legacy_decision_refs\.py:"),
        # tests/test_storage.py G1 docstring 引 ADJUST_ACTIONS drift guard 作历史类比
        re.compile(r"tests/test_storage\.py.*ADJUST_ACTIONS drift guard"),
    ]

    filtered = []
    for h in hits:
        if any(p.search(h) for p in WHITELIST_PATTERNS):
            continue
        filtered.append(h)

    assert not filtered, (
        "派生路线符号残留 (R2-7 §5 应全删):\n" + "\n".join(filtered[:20])
    )


def test_g7_legacy_names_no_residual():
    """G7-2 (R2-7 §14, M3+K extension): DecisionLog / decision_logs 在 src/ + tests/
    内 0 hit (除 whitelist)。Issue 1+2 校准.

    Whitelist (path-anchored regex):
    1. 本测试文件自身（含定义）
    2. tests/test_alembic_migration.py — 历史 Iter 3 + R2-4 migration 行为测试
       (PRAGMA / INSERT decision_logs 是 by design — 验证旧 schema chain 状态)
    3. src/storage/database.py:112 chain 演进描述（含 "(Iter 3) → agent_cycles (R2-7)"）
    4. tests/test_storage.py G1 SoT docstring 引 "AgentCycle (was DecisionLog)"（历史上下文）
    5. tests/test_usage_limits.py t10 deletion tombstone comment（intentional historical）
    """
    paths = [REPO_ROOT / "src", REPO_ROOT / "tests"]
    pattern = r"\b(" + "|".join(LEGACY_NAMES) + r")\b"
    hits = _grep(pattern, paths)

    WHITELIST_PATTERNS = [
        # 测试自身
        re.compile(r"tests/test_drift_no_legacy_decision_refs\.py:"),
        # tests/test_alembic_migration.py 整文件（Iter 3 + R2-4 schema chain step 测试 by design）
        re.compile(r"tests/test_alembic_migration\.py:"),
        # database.py:112 chain 演进描述
        re.compile(r"src/storage/database\.py.*decision_logs \(Iter 3\)"),
        # tests/test_storage.py 历史上下文 docstring "(was DecisionLog)"
        re.compile(r"tests/test_storage\.py.*was DecisionLog"),
        # tests/test_usage_limits.py t10 deletion tombstone (Task 5)
        re.compile(r"tests/test_usage_limits\.py.*test_t10_forensic_path_derives"),
        re.compile(r"tests/test_usage_limits\.py.*trade_actions → DecisionLog\.decision"),
    ]

    filtered = []
    for h in hits:
        if any(p.search(h) for p in WHITELIST_PATTERNS):
            continue
        filtered.append(h)

    assert not filtered, (
        "DecisionLog/decision_logs 残留 (R2-7 应已 rename agent_cycles/AgentCycle):\n"
        + "\n".join(filtered[:20])
    )

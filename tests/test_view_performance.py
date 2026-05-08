"""AC-10: view 性能 — sim #8 178 行级 SELECT < 100ms (offline benchmark)."""
import subprocess

import pytest


def test_benchmark_view_phase1(request):
    """T22.1: 跑 benchmark script 确认所有 view 中位 < 100ms.

    CI skip if --sim-db not present. W3 上线前 manual:
        pytest tests/test_view_performance.py --sim-db data/tradebot.db

    Hard fail by design — manual gate before W3 release; CI 通过 `--sim-db`
    缺失自动 skip 不阻拦 CI（与 spec AC-10 "non CI strict gate" 兼容）。
    """
    db = request.config.getoption("--sim-db")
    if not db:
        pytest.skip("sim DB not present (use --sim-db <path> to run benchmark)")

    result = subprocess.run(
        ["python", "scripts/benchmark_view_phase1.py", "--db", db],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"benchmark failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "✓ All views < 100ms median" in result.stdout

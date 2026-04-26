"""Iter 5 §3.4 — pydantic-ai 版本 pin drift guard."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_pydantic_ai_pinned_to_minor_floor_below_v2():
    """T9: pyproject.toml 中 pydantic-ai constraint 同时含 >=1.78 和 <2。

    防 floor 解 pin（>=1.0 → minor 升级污染观察期）+ 防 ceiling 解 pin
    （<2 删除 → 2.0 major breaking change）。
    """
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    pydantic_ai_constraint = next(
        (d for d in deps if d.startswith("pydantic-ai")), None
    )

    assert pydantic_ai_constraint is not None, "pydantic-ai 不在 dependencies 中"
    assert ">=1.78" in pydantic_ai_constraint, (
        f"floor pin >=1.78 缺失: {pydantic_ai_constraint!r}"
    )
    assert "<2" in pydantic_ai_constraint, (
        f"ceiling pin <2 缺失: {pydantic_ai_constraint!r}"
    )

"""Drift guard for iter-w2r3-memory-disable.

Asserts that memory tool wiring is removed:
(a) save_memory / get_memories not in agent toolset
(b) src/cli/app.py source has no memory injection wiring
(c) generate_system_prompt does not reference deprecated memory tool
(d) MemoryService class and memory_entries table still exist (storage layer untouched)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def test_a_memory_tools_unregistered():
    """(a) save_memory / get_memories must be absent from agent toolset."""
    from src.agent.trader import create_trader_agent, REGISTERED_TOOL_NAMES
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool_names = set(agent._function_toolset.tools)

    assert "save_memory" not in tool_names, "save_memory must be unregistered"
    assert "get_memories" not in tool_names, "get_memories must be unregistered"
    assert "save_memory" not in REGISTERED_TOOL_NAMES
    assert "get_memories" not in REGISTERED_TOOL_NAMES
    assert len(REGISTERED_TOOL_NAMES) == 32, (
        f"Expected 32 tools (19 perception + 13 execution), got {len(REGISTERED_TOOL_NAMES)}"
    )


def test_b_app_py_wiring_removed():
    """(b) src/cli/app.py source must not contain memory injection wiring.

    Static source-code guard. Rationale: the three injection markers
    ('Your memories:' / '=== Long-term Memory ===' / '=== Recent Context ===')
    can only reach the runtime user_prompt via the wiring path in app.py
    (`memory_context = await deps.memory.format_for_prompt(); prompt += "Your memories:\\n" + memory_context`).
    The latter two strings originate from MemoryService.format_for_prompt
    (memory.py:91, 97) which spec preserves — so asserting them absent
    from prompt requires asserting the wiring call site is gone.

    Static source assertion is stronger and simpler than full run_agent_cycle
    mocking (which risks vacuous-pass via incomplete mocks).
    """
    repo_root = Path(__file__).resolve().parent.parent
    app_src = (repo_root / "src" / "cli" / "app.py").read_text(encoding="utf-8")

    assert "Your memories:" not in app_src, (
        "Wiring regression: 'Your memories:' string found in src/cli/app.py"
    )
    assert "deps.memory.format_for_prompt" not in app_src, (
        "Wiring regression: 'deps.memory.format_for_prompt' call found in src/cli/app.py"
    )
    assert "memory_context" not in app_src, (
        "Wiring regression: 'memory_context' variable found in src/cli/app.py"
    )


def test_c_system_prompt_has_no_memory_pointer():
    """(c) generate_system_prompt must not reference deprecated memory tool."""
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig

    prompt = generate_system_prompt(PersonaConfig())

    assert not re.search(r"Save actionable lessons to memory", prompt), (
        "persona.py:89 dead pointer leaked: 'Save actionable lessons to memory.'"
    )
    assert not re.search(r"lessons in your memory", prompt), (
        "persona.py:135 dead pointer leaked: 'Are there relevant lessons in your memory?'"
    )


def test_d_storage_layer_intact():
    """(d) MemoryService class and memory_entries table must still exist.

    Storage layer is out-of-scope for this iter — confirm no over-reach.
    """
    from src.agent.memory import MemoryService  # noqa: F401  (import must succeed)
    from src.storage.models import MemoryEntry  # noqa: F401  (import must succeed)

    assert MemoryEntry.__tablename__ == "memory_entries"
    # MemoryService class methods still callable
    assert hasattr(MemoryService, "save_long_term")
    assert hasattr(MemoryService, "format_for_prompt")
    assert hasattr(MemoryService, "get_relevant_memories")

"""LLM-facing tool descriptions for tools where pydantic-ai 1.78 / griffe
strips structured sections (Examples / Example output / inline admonitions)
from `tool.tool_def.description`.

Constants in this module are passed verbatim via `@tool(description=DESC_X)`
to bypass griffe parsing and reach the LLM. Args descriptions remain in the
source docstring (parsed normally into `parameters_json_schema`).

See docs/superpowers/specs/2026-05-19-iter-tool-opt-dead-example-promote-design.md
for the audit (7 tools / 4 loss categories) + design rationale.
"""

# Constants added by subsequent migration tasks (Tasks 2-6).

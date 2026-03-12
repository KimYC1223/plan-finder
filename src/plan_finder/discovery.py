from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolUseBlock,
    query,
)

from .models import DiscoveredPlan


@dataclass
class DiscoveryResult:
    plan: DiscoveredPlan | None
    cost_usd: float
    total_tokens: int
    session_id: str | None
    model: str | None = None


async def discover_plan(
    prompt: str,
    cwd: str | None = None,
    resume_session_id: str | None = None,
    on_activity: Callable[[str], None] | None = None,
    model: str | None = None,
) -> DiscoveryResult:
    """Run a single Claude query to discover one improvement plan."""
    target_dir = cwd or os.getcwd()

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep", "WebSearch", "Bash"],
        permission_mode="bypassPermissions",
        cwd=target_dir,
        max_turns=200,
        output_format={
            "type": "json_schema",
            "schema": DiscoveredPlan.model_json_schema(),
        },
        system_prompt=(
            "You are in READ-ONLY mode. You may only use Read, Glob, Grep, "
            "WebSearch, and Bash (read-only commands only like ls, git log, wc, etc.). "
            "Do NOT modify any files. Your goal is to analyze the codebase "
            "and produce a structured improvement plan."
        ),
    )

    if model:
        options.model = model

    if resume_session_id:
        options.resume = resume_session_id

    plan: DiscoveredPlan | None = None
    cost: float = 0.0
    tokens: int = 0
    session_id: str | None = None
    model: str | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            if model is None:
                model = message.model
            if on_activity:
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        tool_input = block.input
                        detail = _summarize_tool(block.name, tool_input)
                        on_activity(detail)
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0
            session_id = message.session_id
            if message.usage:
                u = message.usage
                tokens = (
                    u.get("input_tokens", 0)
                    + u.get("output_tokens", 0)
                    + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)
                )
            if message.subtype == "success" and message.structured_output:
                plan = DiscoveredPlan.model_validate(message.structured_output)

    return DiscoveryResult(
        plan=plan, cost_usd=cost, total_tokens=tokens, session_id=session_id,
        model=model,
    )


def _summarize_tool(name: str, inp: dict) -> str:
    """Create a short human-readable summary of a tool call."""
    if name == "Read":
        path = inp.get("file_path", "")
        return f"Reading {_short_path(path)}"
    if name == "Glob":
        return f"Searching {inp.get('pattern', '')}"
    if name == "Grep":
        pattern = inp.get("pattern", "")
        path = inp.get("path", "")
        suffix = f" in {_short_path(path)}" if path else ""
        return f"Grep '{pattern}'{suffix}"
    if name == "Bash":
        cmd = inp.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"$ {cmd}"
    return f"{name}(...)"


def _short_path(path: str) -> str:
    """Shorten a path to last 2 components."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) > 2:
        return "/".join(parts[-2:])
    return path

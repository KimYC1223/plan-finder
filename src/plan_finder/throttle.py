"""Session-aware throttle using cost ($) from ResultMessage.total_cost_usd.

Formula:
  (cumulative_cost / session_budget) * 1.05 < (elapsed / session_duration)

Session timing auto-detected via `ccusage blocks --json`.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta

from . import display

DEFAULT_SESSION_BUDGET = 40.0  # $40 per session


def detect_session() -> dict | None:
    """Auto-detect current session info from ccusage.

    Returns dict with keys:
      session_start: datetime (local)
      session_end: datetime (local)
      cost_usd: float (cost already spent in this session)
      models: list[str] (models used in this session)

    Returns None if ccusage is unavailable or no active session found.
    """
    try:
        json_result = subprocess.run(
            ["ccusage", "blocks", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if json_result.returncode != 0:
            return None

        data = json.loads(json_result.stdout)
        active_block = None

        for block in data.get("blocks", []):
            if block.get("isActive"):
                active_block = block

        if active_block is None:
            return None

        start_utc = datetime.fromisoformat(
            active_block["startTime"].replace("Z", "+00:00")
        )
        end_utc = datetime.fromisoformat(
            active_block["endTime"].replace("Z", "+00:00")
        )
        session_start = start_utc.astimezone().replace(tzinfo=None)
        session_end = end_utc.astimezone().replace(tzinfo=None)

        return {
            "session_start": session_start,
            "session_end": session_end,
            "cost_usd": active_block.get("costUSD", 0.0),
            "models": active_block.get("models", []),
        }

    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
        return None


class SessionThrottle:
    def __init__(
        self,
        session_budget: float = DEFAULT_SESSION_BUDGET,
    ) -> None:
        self.session_budget = session_budget
        self.cumulative_cost: float = 0.0
        self.cumulative_tokens: int = 0
        self.model: str | None = None
        self._init_session()

    def _init_session(self) -> None:
        session_info = detect_session()

        if session_info is None:
            raise RuntimeError(
                "ccusage is required but not available. "
                "Install it with: brew install ccusage"
            )

        self.session_start = session_info["session_start"]
        self.session_end = session_info["session_end"]
        self.session_duration = self.session_end - self.session_start
        self.cumulative_cost = session_info["cost_usd"]
        models = [m for m in session_info.get("models", []) if m != "<synthetic>"]
        if models and self.model is None:
            self.model = models[0]
        display.console.print(
            f"[dim]Session detected via ccusage: "
            f"{self.session_start.strftime('%H:%M')} ~ "
            f"{self.session_end.strftime('%H:%M')}, "
            f"${self.cumulative_cost:.2f}/${self.session_budget:.0f} spent[/dim]"
        )

    def reinit(self) -> None:
        """Re-detect session info (e.g. after session reset)."""
        display.console.print("[dim]Re-detecting session...[/dim]")
        self.cumulative_cost = 0.0
        self.cumulative_tokens = 0
        self._init_session()

    def add_usage(self, cost_usd: float, tokens: int, model: str | None = None) -> None:
        self.cumulative_cost += cost_usd
        self.cumulative_tokens += tokens
        if model and self.model is None:
            self.model = model

    def _elapsed_ratio(self) -> float:
        now = datetime.now()
        elapsed = (now - self.session_start).total_seconds()
        total = self.session_duration.total_seconds()
        return max(0.0, min(1.0, elapsed / total))

    def _usage_ratio(self) -> float:
        if self.session_budget <= 0:
            return 0.0
        return self.cumulative_cost / self.session_budget

    def is_allowed(self) -> bool:
        return self._usage_ratio() * 1.05 < self._elapsed_ratio()

    def seconds_until_allowed(self) -> float:
        usage = self._usage_ratio()
        if usage <= 0:
            return 0.0
        total_secs = self.session_duration.total_seconds()
        elapsed_secs = (datetime.now() - self.session_start).total_seconds()
        needed_elapsed = usage * 1.05 * total_secs
        remaining = max(0.0, needed_elapsed - elapsed_secs)
        # Cap at session end — session resets after that
        time_until_session_end = max(0.0, total_secs - elapsed_secs)
        return min(remaining, time_until_session_end)

    async def wait_if_needed(self) -> None:
        import asyncio

        while not self.is_allowed():
            wait = self.seconds_until_allowed()
            if wait <= 0:
                break
            wait += 30  # buffer to avoid re-triggering
            from datetime import datetime

            now_str = datetime.now().strftime("%H:%M:%S")
            display.console.print(
                f"\n[yellow][{now_str}] Throttling: cost {self._usage_ratio():.0%} * 1.05 "
                f"> time {self._elapsed_ratio():.0%}. "
                f"Waiting {wait / 60:.1f} min...[/yellow]"
            )
            await asyncio.sleep(wait)
            display.console.print("[dim]Throttle wait done, resuming...[/dim]")

    def status_line(self) -> str:
        usage = self._usage_ratio()
        elapsed = self._elapsed_ratio()
        pace = usage * 1.05
        margin = elapsed - pace

        if margin > 0.15:
            indicator = "🟢 Plenty"
        elif margin > 0.05:
            indicator = "🟡 OK"
        elif margin > 0:
            indicator = "🟠 Tight"
        else:
            indicator = "🔴 Over"

        remaining_hours = (
            self.session_duration.total_seconds() * (1 - elapsed) / 3600
        )

        model_str = f" | Model: {self.model}" if self.model else ""

        return (
            f"Cost: ${self.cumulative_cost:.2f}/"
            f"${self.session_budget:.0f} "
            f"({usage:.0%}) | "
            f"Session: {elapsed:.0%} ({remaining_hours:.1f}h left) | "
            f"{indicator} (pace {pace:.0%} vs time {elapsed:.0%})"
            f"{model_str}"
        )

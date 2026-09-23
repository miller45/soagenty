# Generator Env
# --------
# GitHub Copilot coding agent (model: GitHub Copilot)

import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


import openai
from rich.console import Console
from rich.markdown import Markdown

from llm_scenarios import SCENARIOS, get_api_key

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

ACTIVE_SCENARIO = "litellm-GLM-5.2"
CUSTOMREASONING="medium"
HTML_REPORTS="output"
SCRIPT_STARTED_AT = datetime.now().astimezone()
MAX_TOOL_ROUNDS = 25
MAX_CONSECUTIVE_NON_PROGRESS_TOOLS = 5
REQUEST_TIMEOUT_SECONDS = 120
if os.getenv("AI_AGENT_DUMPFULLJSON") is not None:
   DUMPFULLJSON = (os.getenv("AI_AGENT_DUMPFULLJSON") in ("1","true","True"))
else:
   DUMPFULLJSON = False

INITIAL_GRID = [
    [1, 0, 0, 4],
    [0, 4, 1, 0],
    [2, 0, 4, 0],
    [0, 3, 0, 1],
]

clue_cells = [
    (y, x) for y, row in enumerate(INITIAL_GRID) for x, value in enumerate(row) if value != 0
]
sudoku_grid = [row[:] for row in INITIAL_GRID]

finish_requested = False

AUTO_PROMPT = True

def get_log_file(started_at: datetime) -> Path:
    timestamp = started_at.strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parent / "output" / f"tool_call_sudoku-{timestamp}.md"


LOG_FILE = get_log_file(SCRIPT_STARTED_AT)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_sudoku_board",
            "description": "Get the current 4x4 Sudoku board and the fixed clue cells.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_sudoku_cell",
            "description": "Set exactly one cell on the Sudoku board per response. y is the row (0-3), x is the column (0-3), number is 1-4.",
            "parameters": {
                "type": "object",
                "properties": {
                    "y": {"type": "integer", "description": "Row index, 0-3."},
                    "x": {"type": "integer", "description": "Column index, 0-3."},
                    "number": {"type": "integer", "description": "Value to place, 1-4."},
                },
                "required": ["y", "x", "number"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_sudoku_cell",
            "description": "Clear a cell on the Sudoku board back to 0 (empty). y is the row (0-3), x is the column (0-3). Cannot clear clue cells.",
            "parameters": {
                "type": "object",
                "properties": {
                    "y": {"type": "integer", "description": "Row index, 0-3."},
                    "x": {"type": "integer", "description": "Column index, 0-3."},
                },
                "required": ["y", "x"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that you are done working on the board. Call this once you believe the Sudoku is solved or you cannot make further progress. Returns the final board state.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]

SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "You are a cheerful, experienced 4x4 Sudoku solver. You work on one 4x4 "
        "board. Use get_sudoku_board to see the current state, "
        "set_sudoku_cell (y=row, x=column, both 0-indexed) to place digits 1-4. "
        "Call set_sudoku_cell only once per response, then observe its tool result "
        "before placing another digit. "
        "Each row, column, and 2x2 box must contain digits 1-4 exactly once. "
        "and clear_sudoku_cell to erase a mistake. Clue cells are fixed and "
        "protected. Always base your description of the current board on tool "
        "results, not memory. When placing or clearing values, use the tools. "
        "When you believe the puzzle is solved (or you cannot make further "
        "progress), call the finish tool to end the session. The finish tool "
        "returns the final board state. If five consecutive tool calls do not "
        "successfully place or clear a digit, call finish instead of continuing. "
        "Always end with a short summary of "
        "your progress and whether the puzzle is solved."
    ),
}


def get_sudoku_board() -> dict[str, Any]:
    return {
        "board": sudoku_grid,
        "clues": [
            {"y": y, "x": x, "number": sudoku_grid[y][x]} for y, x in clue_cells
        ],
        "solved": is_board_solved(),
    }


def set_sudoku_cell(y: int, x: int, number: int) -> dict[str, Any]:
    if not all(isinstance(v, int) for v in (y, x, number)):
        return {"error": "y, x and number must all be integers"}
    if not 0 <= y <= 3 or not 0 <= x <= 3:
        return {"error": "y (row) and x (column) must be between 0 and 3"}
    if not 1 <= number <= 4:
        return {"error": "number must be between 1 and 4"}
    if (y, x) in clue_cells:
        return {
            "error": f"Cell (y={y}, x={x}) is a fixed clue cell and cannot be changed"
        }
    for column in range(4):
        if sudoku_grid[y][column] == number:
            return {"error": f"Row {y} already contains {number} at column {column}"}
    for row in range(4):
        if sudoku_grid[row][x] == number:
            return {"error": f"Column {x} already contains {number} at row {row}"}
    box_row, box_col = (y // 2) * 2, (x // 2) * 2
    for row in range(box_row, box_row + 2):
        for column in range(box_col, box_col + 2):
            if sudoku_grid[row][column] == number:
                return {
                    "error": f"2x2 box ({box_row // 2}, {box_col // 2}) already "
                    f"contains {number} at (y={row}, x={column})"
                }
    sudoku_grid[y][x] = number
    return {
        "ok": True,
        "board": sudoku_grid,
        "solved": is_board_solved(),
    }


def clear_sudoku_cell(y: int, x: int) -> dict[str, Any]:
    if not all(isinstance(v, int) for v in (y, x)):
        return {"error": "y and x must be integers"}
    if not 0 <= y <= 3 or not 0 <= x <= 3:
        return {"error": "y (row) and x (column) must be between 0 and 3"}
    if (y, x) in clue_cells:
        return {
            "error": f"Cell (y={y}, x={x}) is a fixed clue cell and cannot be cleared"
        }
    sudoku_grid[y][x] = 0
    return {
        "ok": True,
        "board": sudoku_grid,
        "solved": is_board_solved(),
    }


def finish() -> dict[str, Any]:
    global finish_requested
    finish_requested = True
    return {
        "ok": True,
        "board": sudoku_grid,
        "solved": is_board_solved(),
        "message": "Finish requested. The application will exit after this turn.",
    }


def is_board_solved() -> bool:
    complete = list(range(1, 5))
    rows_ok = all(sorted(row) == complete for row in sudoku_grid)
    cols_ok = all(
        sorted(sudoku_grid[r][c] for r in range(4)) == complete for c in range(4)
    )
    boxes_ok = all(
        sorted(
            sudoku_grid[br + r][bc + c] for r in range(2) for c in range(2)
        )
        == complete
        for br in (0, 2)
        for bc in (0, 2)
    )
    return rows_ok and cols_ok and boxes_ok


def reset_board() -> None:
    global sudoku_grid
    sudoku_grid = [row[:] for row in INITIAL_GRID]


def print_board() -> None:
    for y, row in enumerate(sudoku_grid):
        if y % 2 == 0 and y:
            print("-" * 9)
        cells: list[str] = []
        for x, value in enumerate(row):
            if x % 2 == 0 and x:
                cells.append("|")
            cells.append(str(value) if value else ".")
        print(" ".join(cells))


def execute_tool(name: str, arguments_json: str) -> dict[str, Any]:
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as error:
        return {"error": f"Invalid tool arguments: {error.msg}"}

    if not isinstance(arguments, dict):
        return {"error": "Tool arguments must be a JSON object"}
    if name == "get_sudoku_board":
        return get_sudoku_board()
    if name == "set_sudoku_cell":
        return set_sudoku_cell(
            arguments.get("y"),
            arguments.get("x"),
            arguments.get("number"),
        )
    if name == "clear_sudoku_cell":
        return clear_sudoku_cell(
            arguments.get("y"),
            arguments.get("x"),
        )
    if name == "finish":
        return finish()
    return {"error": f"Unknown tool: {name}"}


_ROUND_HEADING_RE = re.compile(
    r"^(?P<direction>Outgoing|Incoming) API payload - round (?P<round>\d+)$"
)

_HTML_CSS = """
:root {
  --bg: #f6f8fa;
  --card: #ffffff;
  --border: #d0d7de;
  --text: #1f2328;
  --muted: #57606a;
  --accent: #0969da;
  --code-bg: #f6f8fa;
  --green-bg: #dafbe1;
  --green-fg: #1a7f37;
  --red-bg: #ffebe9;
  --red-fg: #cf222e;
  --yellow-bg: #fff8c5;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px 16px 48px;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.5;
}
.container { max-width: 1000px; margin: 0 auto; }
.run-header {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 20px;
}
.run-header h1 { margin: 0 0 12px; font-size: 1.35rem; }
table.meta { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
table.meta td { padding: 3px 16px 3px 0; vertical-align: top; }
table.meta td:first-child { color: var(--muted); font-weight: 600; white-space: nowrap; }
.badge {
  display: inline-block;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.7;
  border: 1px solid transparent;
  vertical-align: middle;
}
.badge.ok, .badge.solved { background: var(--green-bg); color: var(--green-fg); }
.badge.error { background: var(--red-bg); color: var(--red-fg); }
.badge.info { background: #ddf4ff; color: var(--accent); }
.badge.warn { background: var(--yellow-bg); color: #7d4e00; }
.badge.neutral { background: var(--code-bg); color: var(--muted); border-color: var(--border); }
.muted { color: var(--muted); font-weight: 400; font-size: 0.82em; }
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 20px;
  margin: 16px 0;
}
ol.rounds { list-style: none; margin: 0; padding: 0; }
li.round { margin-bottom: 14px; }
details.round-details {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
details.round-details > summary {
  cursor: pointer;
  padding: 12px 18px;
  font-weight: 700;
  font-size: 1rem;
  background: #fafbfc;
  border-bottom: 1px solid var(--border);
  list-style: none;
}
details.round-details > summary::-webkit-details-marker { display: none; }
details.round-details > summary::before { content: "\\25B8 "; color: var(--muted); }
details.round-details[open] > summary::before { content: "\\25BE "; }
.round-body { padding: 14px 18px 18px; }
.step { margin: 14px 0; }
.step:first-child { margin-top: 0; }
.step-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-weight: 650;
  font-size: 0.92rem;
  margin-bottom: 8px;
}
pre.json {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  overflow-x: auto;
  font-size: 0.78rem;
  line-height: 1.5;
  margin: 0;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
}
pre.block {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 0.88rem;
  margin: 0;
}
pre.block.thinking { font-style: italic; color: #444c56; }
details.raw { margin-top: 8px; }
details.raw > summary { cursor: pointer; color: var(--accent); font-size: 0.82rem; }
table.sudoku {
  border-collapse: collapse;
  border: 3px solid #24292f;
  background: var(--card);
  margin: 4px 0 8px;
}
table.sudoku td {
  width: 3rem;
  height: 3rem;
  text-align: center;
  vertical-align: middle;
  font-size: 1.25rem;
  font-weight: 700;
  border: 1px solid #9aa4af;
}
table.sudoku td.box-top { border-top: 3px solid #24292f; }
table.sudoku td.box-left { border-left: 3px solid #24292f; }
table.sudoku td.clue { background: #e4edf9; }
table.sudoku td.changed { background: var(--yellow-bg); }
.toolcall-card { border-left: 3px solid var(--accent); padding-left: 12px; margin: 10px 0; }
.toolresult-card { border-left: 3px solid var(--green-fg); padding-left: 12px; margin: 10px 0; }
.toolresult-card.error { border-left-color: var(--red-fg); }
.error-box {
  background: var(--red-bg);
  border: 1px solid rgba(255, 129, 130, 0.4);
  color: var(--red-fg);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 0.9rem;
}
.banner { border-radius: 12px; padding: 14px 20px; margin: 16px 0; font-weight: 600; }
.banner.success { background: var(--green-bg); color: var(--green-fg); }
.banner.failure { background: var(--red-bg); color: var(--red-fg); }
.banner.notice { background: #ddf4ff; color: var(--accent); }
.run-summary { margin-top: 24px; }
footer.run-footer { margin-top: 24px; color: var(--muted); font-size: 0.8rem; text-align: center; }
"""


def _html_escape(value: Any) -> str:
    return html_lib.escape(str(value))


def _try_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _fmt_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def get_html_report_file(started_at: datetime) -> Path | None:
    if HTML_REPORTS is None or not str(HTML_REPORTS).strip():
        return None
    base = Path(str(HTML_REPORTS))
    if not base.is_absolute():
        base = Path(__file__).resolve().parent / base
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    return base / f"tool_call_sudoku-{stamp}.html"


class HtmlReport:
    """Self-contained HTML report written incrementally during the run."""

    def __init__(
        self,
        path: Path,
        started_at: datetime,
        clues: list[tuple[int, int]],
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self._clues = set(clues)
        self._started_at = started_at
        self._meta = meta or {}
        self._round = 0
        self._rounds = 0
        self._tool_calls = 0
        self._errors = 0
        self._solved = False
        self._finish_called = False
        self._last_board: list[list[int]] | None = None
        self._rounds_opened = False
        self._closed = False
        self._api_time_total = 0.0
        self._api_rounds = 0
        self._pending_api_seconds: float | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8", newline="\n")
        self._write(
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_html_escape(self._meta.get('title', 'Sudoku tool-call run'))}</title>\n"
            f"<style>{_HTML_CSS}</style>\n"
            "</head>\n"
            "<body>\n"
            '<div class="container">\n'
        )
        self._write(
            '<header class="run-header">\n'
            "<h1>4&times;4 Sudoku &mdash; LLM tool-call history</h1>\n"
            '<table class="meta">\n'
            f"<tr><td>Started</td><td>{_html_escape(started_at.isoformat(timespec='seconds'))}</td></tr>\n"
        )
        for key in ("Scenario", "Model", "Endpoint", "Reasoning effort", "Markdown transcript"):
            if self._meta.get(key):
                self._write(
                    f"<tr><td>{_html_escape(key)}</td><td>{_html_escape(self._meta[key])}</td></tr>\n"
                )
        self._write(f"<tr><td>HTML report</td><td>{_html_escape(path.name)}</td></tr>\n")
        self._write("</table>\n</header>\n")

    def record_json(self, heading: str, payload: Any) -> None:
        when = datetime.now().astimezone()
        match = _ROUND_HEADING_RE.match(heading)
        if match and match.group("direction") == "Outgoing":
            self._open_round(int(match.group("round")), when)
            self._render_outgoing(payload, when)
            return
        if match and match.group("direction") == "Incoming":
            self._render_incoming(payload, when)
            return
        if heading.startswith("Local tool result - "):
            name = heading.removeprefix("Local tool result - ")
            self._render_tool_result(name, payload, when)
            return
        if heading == "Request error":
            self._errors += 1
            self._write(
                '<section class="step">\n'
                '<div class="step-title">Request error '
                f'<span class="badge error">{_html_escape(payload.get("type", "error"))}</span>'
                f'<span class="muted">&middot; {when:%H:%M:%S}</span></div>\n'
                f'<div class="error-box">{_html_escape(payload.get("message", ""))}</div>\n'
                "</section>\n"
            )
            return
        self._render_card(heading, payload, when, as_json=True)

    def record_text(self, heading: str, text: str) -> None:
        when = datetime.now().astimezone()
        if heading == "Initial board":
            board = _try_json_loads(text)
            self._write('<section class="card">\n<div class="step-title">Initial board</div>\n')
            if isinstance(board, list) and len(board) == 4:
                self._write(self._board_table(board))
                self._last_board = [row[:] for row in board]
            else:
                self._write(f'<pre class="block">{_html_escape(text)}</pre>\n')
            self._write("</section>\n")
            return
        if heading == "Finished":
            self._finish_called = True
            self._close_round_and_list()
            first_line, _, rest = text.partition("\n")
            solved = "Solved: True" in first_line
            if solved:
                self._solved = True
            self._write(
                f'<section class="banner {"success" if solved else "failure"}">'
                "Finish tool called by the model &mdash; "
                f"{'puzzle solved' if solved else 'not fully solved'}.</section>\n"
            )
            board = _try_json_loads(rest)
            if isinstance(board, list) and len(board) == 4:
                self._write('<section class="card">\n<div class="step-title">Final board</div>\n')
                self._write(self._board_table(board))
                self._write("</section>\n")
            return
        if heading == "Solved":
            self._solved = True
            self._close_round_and_list()
            self._write('<section class="banner success">Board solved &mdash; auto prompt loop stopped.</section>\n')
            if self._last_board is not None:
                self._write('<section class="card">\n<div class="step-title">Final board</div>\n')
                self._write(self._board_table(self._last_board))
                self._write("</section>\n")
            return
        if heading == "Conversation reset":
            self._close_round_and_list()
            self._last_board = None
            self._write('<section class="banner notice">Conversation and board reset.</section>\n')
            return
        self._render_card(heading, text, when, as_json=False)

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_round_and_list()
        ended_at = datetime.now().astimezone()
        if self._solved:
            badge, label = "solved", "Solved"
        elif self._finish_called:
            badge, label = "warn", "Finished (not fully solved)"
        elif self._errors:
            badge, label = "error", "Stopped after error"
        else:
            badge, label = "neutral", "Incomplete"
        self._write(
            '<section class="card run-summary">\n'
            '<div class="step-title">Run summary</div>\n'
            '<table class="meta">\n'
            f'<tr><td>Result</td><td><span class="badge {badge}">{label}</span></td></tr>\n'
            f"<tr><td>Rounds</td><td>{self._rounds}</td></tr>\n"
            f"<tr><td>Tool calls</td><td>{self._tool_calls}</td></tr>\n"
            f"<tr><td>Errors</td><td>{self._errors}</td></tr>\n"
            f"<tr><td>API time</td><td>{self._api_time_total:.2f}s over {self._api_rounds} round(s)</td></tr>\n"
            f"<tr><td>Ended</td><td>{_html_escape(ended_at.isoformat(timespec='seconds'))}</td></tr>\n"
            f"<tr><td>Duration</td><td>{_fmt_duration(ended_at - self._started_at)}</td></tr>\n"
            "</table>\n"
            "</section>\n"
            f'<footer class="run-footer">Generated by tool_call_sudoku.py &middot; '
            f"{_html_escape(ended_at.isoformat(timespec='seconds'))}</footer>\n"
            "</div>\n"
            "</body>\n"
            "</html>\n"
        )
        self._fh.close()

    def note_request_timing(self, round_number: int, seconds: float) -> None:
        self._api_time_total += seconds
        self._api_rounds += 1
        self._pending_api_seconds = seconds

    def _open_round(self, round_number: int, when: datetime) -> None:
        if not self._rounds_opened:
            self._write('<ol class="rounds">\n')
            self._rounds_opened = True
        self._close_round()
        self._round = round_number
        self._rounds += 1
        self._write(
            f'<li class="round" id="round-{round_number}">\n'
            '<details class="round-details" open>\n'
            f"<summary>Round {round_number} <span class='muted'>&middot; {when:%H:%M:%S}</span></summary>\n"
            '<div class="round-body">\n'
        )

    def _close_round(self) -> None:
        if self._round:
            self._write("</div>\n</details>\n</li>\n")
            self._round = 0

    def _close_round_and_list(self) -> None:
        self._close_round()
        if self._rounds_opened:
            self._write("</ol>\n")
            self._rounds_opened = False

    def _render_outgoing(self, payload: dict[str, Any], when: datetime) -> None:
        messages = payload.get("messages") or []
        self._write(
            '<section class="step">\n'
            '<div class="step-title">Outgoing request '
            f'<span class="muted">{len(messages)} message(s) in history &middot; '
            f"model {_html_escape(payload.get('model', '?'))} &middot; {when:%H:%M:%S}</span></div>\n"
            '<details class="raw"><summary>raw payload</summary>\n'
            f'<pre class="json">{_html_escape(json.dumps(payload, indent=2, ensure_ascii=False))}</pre>\n'
            "</details>\n"
            "</section>\n"
        )

    def _render_incoming(self, payload: dict[str, Any], when: datetime) -> None:
        message = payload.get("message") or {}
        finish_reason = payload.get("finish_reason") or "unknown"
        reasoning = message.get("reasoning_content")
        content = message.get("content")
        tool_calls = message.get("tool_calls") or []
        api_badge = ""
        if self._pending_api_seconds is not None:
            api_badge = f' <span class="badge neutral">API: {self._pending_api_seconds:.2f}s</span>'
            self._pending_api_seconds = None
        self._write(
            '<section class="step">\n'
            '<div class="step-title">Assistant response '
            f'<span class="badge info">finish_reason: {_html_escape(finish_reason)}</span>'
            f"{api_badge}"
            f'<span class="muted">&middot; {when:%H:%M:%S}</span></div>\n'
        )
        if reasoning:
            self._write(
                '<details class="raw" open><summary>thinking</summary>\n'
                f'<pre class="block thinking">{_html_escape(reasoning)}</pre>\n'
                "</details>\n"
            )
        if content:
            self._write(f'<pre class="block">{_html_escape(content)}</pre>\n')
        for tool_call in tool_calls:
            self._tool_calls += 1
            function = tool_call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.dumps(json.loads(raw_arguments), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                arguments = raw_arguments
            self._write(
                '<div class="toolcall-card">\n'
                '<div class="step-title">Tool call: '
                f"{_html_escape(function.get('name', '?'))} "
                f'<span class="muted">id {_html_escape(tool_call.get("id", ""))}</span></div>\n'
                f'<pre class="json">{_html_escape(arguments)}</pre>\n'
                "</div>\n"
            )
        self._write("</section>\n")

    def _render_tool_result(self, name: str, payload: dict[str, Any], when: datetime) -> None:
        if name.startswith("finish"):
            self._finish_called = True
        is_error = "error" in payload
        is_ok = payload.get("ok") is True
        badge = "ok" if is_ok else ("error" if is_error else "neutral")
        board = payload.get("board")
        changed: set[tuple[int, int]] | None = None
        if isinstance(board, list) and len(board) == 4 and self._last_board is not None:
            changed = {
                (y, x)
                for y in range(4)
                for x in range(4)
                if board[y][x] != self._last_board[y][x]
            }
            if not changed:
                changed = None
        if payload.get("solved"):
            self._solved = True
        self._write(
            '<section class="step">\n'
            f'<div class="toolresult-card{" error" if is_error else ""}">\n'
            '<div class="step-title">Tool result: '
            f"{_html_escape(name)} <span class=\"badge {badge}\">{badge}</span>"
            f'<span class="muted">&middot; {when:%H:%M:%S}</span></div>\n'
        )
        if is_error:
            self._write(f'<div class="error-box">{_html_escape(payload.get("error"))}</div>\n')
        if isinstance(board, list) and len(board) == 4:
            self._write(self._board_table(board, changed))
        self._write(
            '<details class="raw"><summary>raw result</summary>\n'
            f'<pre class="json">{_html_escape(json.dumps(payload, indent=2, ensure_ascii=False))}</pre>\n'
            "</details>\n"
            "</div>\n"
            "</section>\n"
        )
        if isinstance(board, list) and len(board) == 4:
            self._last_board = [row[:] for row in board]

    def _render_card(self, heading: str, payload: Any, when: datetime, as_json: bool) -> None:
        self._write(
            '<section class="card">\n'
            f'<div class="step-title">{_html_escape(heading)} <span class="muted">&middot; {when:%H:%M:%S}</span></div>\n'
        )
        if as_json:
            self._write(
                f'<pre class="json">{_html_escape(json.dumps(payload, indent=2, ensure_ascii=False))}</pre>\n'
            )
        else:
            self._write(f'<pre class="block">{_html_escape(payload)}</pre>\n')
        self._write("</section>\n")

    def _board_table(self, board: list[list[int]], changed: set[tuple[int, int]] | None = None) -> str:
        changed = changed or set()
        parts = ['<table class="sudoku">\n']
        for y in range(4):
            parts.append("<tr>\n")
            for x in range(4):
                value = board[y][x]
                classes = []
                if y == 2:
                    classes.append("box-top")
                if x == 2:
                    classes.append("box-left")
                if (y, x) in self._clues:
                    classes.append("clue")
                if (y, x) in changed:
                    classes.append("changed")
                attrs = f' class="{" ".join(classes)}"' if classes else ""
                parts.append(f"<td{attrs}>{value if value else ''}</td>")
            parts.append("</tr>\n")
        parts.append("</table>\n")
        return "".join(parts)

    def _write(self, chunk: str) -> None:
        if self._fh is not None:
            self._fh.write(chunk)
            self._fh.flush()


class Transcript:
    def __init__(
        self,
        path: Path,
        started_at: datetime | None = None,
        html: HtmlReport | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._html = html
        launch_time = started_at or datetime.now().astimezone()
        started_marker = launch_time.isoformat(timespec="seconds")
        self._append(f"\n# Sudoku Agent Run\n\nStarted: {started_marker}\n")

    def record_json(self, heading: str, payload: Any) -> None:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        self._append(f"\n## {heading}\n\n```json\n{serialized}\n```\n")
        if DUMPFULLJSON:
            print(f"\n--- {heading} ---")
            print(serialized)
        self._forward_to_html("record_json", heading, payload)

    def record_text(self, heading: str, text: str) -> None:
        self._append(f"\n## {heading}\n\n{text}\n")
        self._forward_to_html("record_text", heading, text)

    def note_request_timing(self, round_number: int, seconds: float) -> None:
        self._forward_to_html("note_request_timing", round_number, seconds)

    def _forward_to_html(self, method: str, *args: Any) -> None:
        if self._html is None:
            return
        try:
            getattr(self._html, method)(*args)
        except Exception as error:
            print(f"HTML report update failed: {error}", file=sys.stderr)

    def _append(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(text)


def compact_response(response: Any) -> dict[str, Any]:
    choice = response.choices[0]
    return {
        "id": response.id,
        "model": response.model,
        "finish_reason": choice.finish_reason,
        "message": choice.message.model_dump(exclude_none=True),
    }


def complete_turn(
    client: openai.OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    transcript: Transcript,
    console: Console,
) -> str:
    consecutive_non_progress_tools = 0
    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        request_payload = {
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
        }
        if CUSTOMREASONING is not None:
            #request_payload["extra_body"] = {"reasoning_effort": CUSTOMREASONING}
            request_payload["reasoning_effort"]=CUSTOMREASONING

        transcript.record_json(
            f"Outgoing API payload - round {round_number}", request_payload
        )

        console.print(
            f"[yellow]Request round {round_number} started; waiting for the API "
            f"(timeout: {REQUEST_TIMEOUT_SECONDS}s).[/]"
        )
        request_started_at = datetime.now()
        with console.status(
            f"Request round {round_number} is running (timeout: {REQUEST_TIMEOUT_SECONDS}s)..."
        ):
            response = client.chat.completions.create(
                **request_payload, timeout=REQUEST_TIMEOUT_SECONDS
            )
        request_seconds = (datetime.now() - request_started_at).total_seconds()
        console.print(
            f"[green]Request round {round_number} completed ({request_seconds:.2f}s).[/]"
        )
        transcript.note_request_timing(round_number, request_seconds)
        response_payload = compact_response(response)
        transcript.record_json(
            f"Incoming API payload - round {round_number}", response_payload
        )

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            console.print(f"\n[dim italic]Thinking (round {round_number}): {reasoning}[/]")
        if message.content and message.tool_calls:
            console.print(f"\n[cyan]-- thoughts before tool calls (round {round_number}) --[/]")
            console.print(Markdown(message.content))

        if not message.tool_calls:
            return message.content or ""

        set_sudoku_cell_called = False
        for tool_call in message.tool_calls:
            if tool_call.function.name == "set_sudoku_cell" and set_sudoku_cell_called:
                result = {
                    "error": "Only one set_sudoku_cell call is allowed per response"
                }
            else:
                result = execute_tool(
                    tool_call.function.name,
                    tool_call.function.arguments,
                )
            if tool_call.function.name == "set_sudoku_cell":
                set_sudoku_cell_called = True
            transcript.record_json(
                f"Local tool result - {tool_call.function.name}", result
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            made_progress = (
                tool_call.function.name in {"set_sudoku_cell", "clear_sudoku_cell"}
                and result.get("ok") is True
            )
            consecutive_non_progress_tools = (
                0 if made_progress else consecutive_non_progress_tools + 1
            )
            if finish_requested:
                return message.content or ""
            if consecutive_non_progress_tools >= MAX_CONSECUTIVE_NON_PROGRESS_TOOLS:
                result = finish()
                transcript.record_json(
                    "Local tool result - finish (non-progress safety limit)", result
                )
                return message.content or ""

    raise RuntimeError(f"Model exceeded the limit of {MAX_TOOL_ROUNDS} tool rounds")


def main() -> None:
    try:
        scenario = SCENARIOS[ACTIVE_SCENARIO]
    except KeyError as error:
        available = ", ".join(sorted(SCENARIOS))
        raise ValueError(
            f"Unknown scenario '{ACTIVE_SCENARIO}'. Available scenarios: {available}"
        ) from error

    api_key, credential_service = get_api_key(scenario)
    credential_source = credential_service or scenario["api_key_env"]
    model = scenario["model"]
    print(
        f"Scenario '{ACTIVE_SCENARIO}': talking to LM at {scenario['base_url']} "
        f"with API key from {credential_source}"
    )
    print(f"Transcript: {LOG_FILE}")
    print("Commands: /reset clears chat history; /board prints the board; /exit or an empty prompt exits.")

    client = openai.OpenAI(
        api_key=api_key,
        base_url=scenario["base_url"],
        max_retries=0,
    )
    console = Console()
    html_report: HtmlReport | None = None
    html_report_path = get_html_report_file(SCRIPT_STARTED_AT)
    if html_report_path is not None:
        html_report = HtmlReport(
            html_report_path,
            SCRIPT_STARTED_AT,
            clue_cells,
            meta={
                "title": f"Sudoku tool-call run {SCRIPT_STARTED_AT.isoformat(timespec='seconds')}",
                "Scenario": ACTIVE_SCENARIO,
                "Model": model,
                "Endpoint": scenario["base_url"],
                "Reasoning effort": CUSTOMREASONING or "default",
                "Markdown transcript": LOG_FILE.name,
            },
        )
        print(f"HTML report: {html_report_path}")
    transcript = Transcript(LOG_FILE, SCRIPT_STARTED_AT, html=html_report)
    transcript.record_text("Initial board", json.dumps(sudoku_grid, indent=2))
    print("\nInitial board:")
    print_board()
    messages: list[dict[str, Any]] = [SYSTEM_MESSAGE.copy()]

    try:
        while True:
            if AUTO_PROMPT:
               prompt = "Please solve this sudoku board by observing the state and setting the unsolved cells. Please say when you are finished"
            else:
               prompt = input(f"\n({model}) You: ").strip()

            if not prompt or prompt == "/exit":
                return
            if prompt == "/board":
                print_board()
                continue
            if prompt == "/reset":
                messages = [SYSTEM_MESSAGE.copy()]
                reset_board()
                print("Conversation and board reset.")
                print_board()
                transcript.record_text("Conversation reset", "Chat history and board were reset.")
                continue

            messages.append({"role": "user", "content": prompt})
            try:
                answer = complete_turn(client, model, messages, transcript, console)
            except Exception as error:
                transcript.record_json(
                    "Request error",
                    {"type": type(error).__name__, "message": str(error)},
                )
                console.print(f"[bold red]Request failed:[/] {error}")
                if AUTO_PROMPT:
                    transcript.record_text(
                        "Stopped after request error",
                        "Auto prompt mode stops after a request error to avoid retrying indefinitely.",
                    )
                    return
                continue

            print("\nAssistant:")
            console.print(Markdown(answer))

            if AUTO_PROMPT and finish_requested:
                print("\nFinish tool called by model. Final board:")
                print_board()
                transcript.record_text(
                    "Finished",
                    f"Model called finish. Solved: {is_board_solved()}\n"
                    + json.dumps(sudoku_grid, indent=2),
                )
                break

            if AUTO_PROMPT and is_board_solved():
                print("\nBoard is solved - stopping auto loop.")
                transcript.record_text("Solved", "Board is solved; stopping auto prompt loop.")
                break
    finally:
        if html_report is not None:
            html_report.finalize()


if __name__ == "__main__":
    main()
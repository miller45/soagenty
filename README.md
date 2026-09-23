# soagenty

Observing the history of LLM tool calling. A small sandbox that lets an LLM solve a 4x4 Sudoku puzzle exclusively through tool calls (`get_sudoku_board`, `set_sudoku_cell`, ...), logging every round trip and rendering nicely formatted HTML reports per step.

## What it does

- Drives an LLM (OpenAI-compatible endpoint via configurable scenarios) to solve a Sudoku board step by step
- Enforces limits such as `MAX_TOOL_ROUNDS` and non-progress detection
- Writes a Markdown log and, when enabled, an HTML report per step so tool-calling behavior can be reviewed over time

## Files

- `tool_call_sudoku-opencode-fp8.py` — the main experiment script (generated with opencode / qwen3.8-27b-fp8)
- `llm_scenarios.py` — model scenarios (base URL, model name, API key lookup via Windows Credential Manager or env var)
- `tool_call_sudoku_prompts.md` — verbatim log of user prompts from an opencode session

## Usage

```powershell
.venv\Scripts\Activate.ps1
python tool_call_sudoku-opencode-fp8.py
```

Configuration is done via the global variables at the top of the script:

- `ACTIVE_SCENARIO` — which entry of `SCENARIOS` to use
- `HTML_REPORTS` — set to an output directory to enable HTML reports; output files are named `tool_call_sudoku-<iso-timestamp>.html`

## Requirements

- Python 3 with a `.venv` containing `openai`, `rich`, and `keyring`
- An API key for the configured scenario (Windows Credential Manager or environment variable)

## License

MIT — see [LICENSE](LICENSE).

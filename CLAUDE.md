# CLAUDE.md — Yazio MCP + Telegram Bot

## Project overview

Two components run in a single Render web service:

1. **MCP server** (`mcp_server.py`) — exposes Yazio nutrition data over the MCP Streamable HTTP transport at `/mcp`. Used by Claude Desktop via `mcp-remote`.
2. **Telegram bot** (`telegram_bot.py`) — answers nutrition questions. Uses Groq (LLaMA 3.3 70B) as the LLM and calls the MCP server for data.

On Render, `mcp_server.py` is the entrypoint. It starts the Telegram bot in a background daemon thread, then launches uvicorn.

## Key files

```
mcp_server.py       FastMCP server + entrypoint
telegram_bot.py     Telegram bot (Groq + MCP client)
requirements.txt    Runtime dependencies
render.yaml         Render deployment (single web service)
```

## Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `YAZIO_EMAIL` | mcp_server.py | Yazio account email |
| `YAZIO_PASSWORD` | mcp_server.py | Yazio account password |
| `TELEGRAM_BOT_TOKEN` | telegram_bot.py | From @BotFather |
| `GROQ_API_KEY` | telegram_bot.py | From console.groq.com |
| `PORT` | mcp_server.py | Set automatically by Render |
| `RENDER` | mcp_server.py | Set automatically by Render; triggers HTTP mode |
| `RENDER_EXTERNAL_HOSTNAME` | mcp_server.py | Used for TransportSecuritySettings |
| `MCP_URL` | telegram_bot.py | Override MCP endpoint (default: https://yazio-mcp.onrender.com/mcp) |

## How the bot works

```
User message (Telegram)
  → ask_groq()
    → fetch MCP tool definitions (cached after first call)
    → Groq chat.completions.create() with tools
    → if tool_use: _call_tool() → MCP /mcp endpoint → Yazio API
    → loop until text response
  → reply to user
```

Tool definitions are fetched from the live MCP server at first message and cached in `_mcp_tools`. This means the bot automatically picks up any new tools added to the MCP server.

## Startup flow on Render

```python
# mcp_server.py __main__
if os.environ.get("RENDER"):
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        threading.Thread(target=bot_main, daemon=True).start()
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=PORT)
else:
    mcp.run()  # stdio mode for local Claude Desktop
```

The Telegram bot is only started if `TELEGRAM_BOT_TOKEN` is set, so the MCP server works standalone without it.

## Important gotchas

- **Signal handlers**: `app.run_polling(stop_signals=None)` is required when the bot runs in a background thread. Without it, `python-telegram-bot` crashes trying to register OS signal handlers from a non-main thread.
- **Host validation**: FastMCP rejects requests with unrecognised `Host` headers. `TransportSecuritySettings(allowed_hosts=[...])` must include the Render hostname.
- **Render binding**: FastMCP's built-in runner binds to `127.0.0.1`. We bypass it with `uvicorn.Config(..., host="0.0.0.0")`.
- **Groq model**: currently `llama-3.3-70b-versatile`. Change the `MODEL` constant in `telegram_bot.py` to switch.

## Nutrition data output format

When presenting nutrition data to the user (meals, macros, daily logs), always:

1. **Render an interactive HTML widget** using `show_widget` with:
   - Summary metric cards: total kcal, avg daily kcal, avg daily protein, avg daily carbs, avg daily fat
   - A Chart.js bar chart of daily calories
   - A 3-colour macro bar under each day header (blue = carbs, green = protein, orange = fat)
   - Day blocks grouped by meal (Breakfast / Lunch / Dinner / Snack) with coloured badges
   - Per-item rows: food name, grams, kcal, carbs, protein, fat

2. **Generate a PDF** using WeasyPrint (`pip install weasyprint --break-system-packages`) from a matching HTML file, saved to `/Users/marky/Projects/yazio/` with a descriptive filename. Present it with `mcp__cowork__present_files`.

Recipes (items with `amount_g: null`) show `—` in the grams column and a small `recipe` label.

## Adding new MCP tools

Add a new `@mcp.tool()` function in `mcp_server.py`. The Telegram bot picks it up automatically on next restart (tool list is fetched live from the MCP endpoint).

## Local development

```bash
source .venv/bin/activate
export YAZIO_EMAIL=... YAZIO_PASSWORD=...
python mcp_server.py   # runs in stdio mode, connect via Claude Desktop
```

To test the Telegram bot locally, also export `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, and `RENDER=1`, then run `python mcp_server.py`.

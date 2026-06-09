# Yazio MCP + Telegram Bot

A self-hosted nutrition assistant that exposes your [Yazio](https://www.yazio.com) food logs as an MCP server and a Telegram bot.

## What's inside

| File | Purpose |
|------|---------|
| `mcp_server.py` | FastMCP server — serves Yazio data over HTTP (Streamable HTTP transport) |
| `telegram_bot.py` | Telegram bot — answers nutrition questions via Groq + MCP tools |
| `render.yaml` | Render deployment config (single web service runs both) |

## Architecture

```
Claude Desktop  ──mcp-remote──▶  /mcp endpoint  ──▶  Yazio API
                                       ▲
Telegram  ──▶  Bot  ──▶  Groq LLM ────┘
```

The MCP server and the Telegram bot run in the same Render process. The bot calls the MCP endpoint to fetch data rather than hitting the Yazio API directly.

## MCP tools

| Tool | Description |
|------|-------------|
| `get_today_meals` | All meals logged today |
| `get_meals_for_date` | Meals for a specific date (YYYY-MM-DD) |
| `get_meals_for_range` | Meals over a date range |
| `get_daily_summary` | Daily calorie + macro totals for the last N days |

## Deploy to Render

1. Fork/clone this repo and push to GitHub.
2. Create a new **Web Service** on [render.com](https://render.com) pointing to the repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `python mcp_server.py`
3. Set the following environment variables in the Render dashboard:

| Variable | Required | Description |
|----------|----------|-------------|
| `YAZIO_EMAIL` | ✅ | Your Yazio account email |
| `YAZIO_PASSWORD` | ✅ | Your Yazio account password |
| `TELEGRAM_BOT_TOKEN` | ✅ | From [@BotFather](https://t.me/BotFather) on Telegram |
| `GROQ_API_KEY` | ✅ | Free at [console.groq.com](https://console.groq.com) |

4. Deploy. The MCP endpoint will be live at `https://<your-service>.onrender.com/mcp`.

## Connect to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yazio": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://<your-service>.onrender.com/mcp"
      ]
    }
  }
}
```

Requires Node 20+. Install via `nvm install 20 && nvm alias default 20`.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export YAZIO_EMAIL=you@example.com
export YAZIO_PASSWORD=yourpassword
python mcp_server.py
```

The server will start in stdio mode (for use with Claude Desktop directly).

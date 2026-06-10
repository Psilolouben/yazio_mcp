#!/usr/bin/env python3
"""
Telegram bot — fetches Yazio data from the MCP server and answers via Groq.

Required environment variables:
    TELEGRAM_BOT_TOKEN      From @BotFather
    GROQ_API_KEY            From console.groq.com
"""

import asyncio
import os
import logging
from datetime import date, timedelta

from groq import AsyncGroq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logging.basicConfig(level=logging.INFO)

MCP_URL = os.environ.get("MCP_URL", "https://yazio-mcp.onrender.com/mcp")
MODEL   = "llama-3.3-70b-versatile"

# ── Groq ──────────────────────────────────────────────────────────────────────

_groq: AsyncGroq | None = None


def _get_groq() -> AsyncGroq:
    global _groq
    if _groq is None:
        _groq = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    return _groq


# ── MCP client ────────────────────────────────────────────────────────────────

async def _call_tool(name: str, inputs: dict) -> str:
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, inputs)
            return result.content[0].text if result.content else "{}"


async def _fetch_context() -> str:
    """Pre-fetch the last 7 days of meals and daily summaries."""
    today     = date.today()
    week_ago  = (today - timedelta(days=6)).isoformat()
    meals, summary = await asyncio.gather(
        _call_tool("get_meals_for_range", {"start_date": week_ago, "end_date": today.isoformat()}),
        _call_tool("get_daily_summary",   {"days": 7}),
    )
    return f"Daily summaries (last 7 days):\n{summary}\n\nDetailed meals (last 7 days):\n{meals}"


# ── LLM ───────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a personal nutrition assistant.
Answer the user's question using only the data below. Be concise.
Today's date is {today}.

--- NUTRITION DATA ---
{data}
---------------------"""


async def ask_groq(user_message: str) -> str:
    context = await _fetch_context()
    system  = SYSTEM_PROMPT.format(today=date.today().isoformat(), data=context)

    response = await _get_groq().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message},
        ],
    )
    return response.choices[0].message.content or "Sorry, I couldn't generate a response."


# ── Telegram ──────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action("typing")
    try:
        reply = await ask_groq(update.message.text)
    except Exception as e:
        logging.exception("Error handling message")
        reply = f"Error: {e}"
    await update.message.reply_text(reply)


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started.")
    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()

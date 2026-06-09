#!/usr/bin/env python3
"""
Telegram bot — routes questions through the Yazio MCP server.
Uses Google Gemini as the LLM (free tier).

Required environment variables:
    TELEGRAM_BOT_TOKEN      From @BotFather
    GEMINI_API_KEY          From aistudio.google.com
"""

import os
import logging
from datetime import date

from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logging.basicConfig(level=logging.INFO)

MCP_URL = os.environ.get("MCP_URL", "https://yazio-mcp.onrender.com/mcp")

# ── Gemini ────────────────────────────────────────────────────────────────────

_gemini: genai.Client | None = None


def _get_gemini() -> genai.Client:
    global _gemini
    if _gemini is None:
        _gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini


# ── MCP client ────────────────────────────────────────────────────────────────

_mcp_tools: list[dict] | None = None


async def _list_tools() -> list[dict]:
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in result.tools
            ]


async def _call_tool(name: str, inputs: dict) -> str:
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, inputs)
            return result.content[0].text if result.content else "{}"


async def get_tools() -> list[dict]:
    global _mcp_tools
    if _mcp_tools is None:
        _mcp_tools = await _list_tools()
    return _mcp_tools


def _to_gemini_tools(mcp_tools: list[dict]) -> list[types.Tool]:
    declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["input_schema"],
        )
        for t in mcp_tools
    ]
    return [types.Tool(function_declarations=declarations)]


# ── LLM loop ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a personal nutrition assistant.
You have access to the user's Yazio nutrition logs via tools.
Answer concisely. Today's date is {today}."""


async def ask_gemini(user_message: str) -> str:
    mcp_tools = await get_tools()
    gemini_tools = _to_gemini_tools(mcp_tools)
    system = SYSTEM_PROMPT.format(today=date.today().isoformat())

    contents = [types.Content(role="user", parts=[types.Part(text=user_message)])]

    while True:
        response = await _get_gemini().aio.models.generate_content(
            model="gemini-1.5-flash-latest",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=gemini_tools,
            ),
        )

        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = [p for p in candidate.content.parts if p.function_call]
        if function_calls:
            tool_responses = []
            for part in function_calls:
                fc = part.function_call
                result = await _call_tool(fc.name, dict(fc.args))
                tool_responses.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=tool_responses))
        else:
            for part in candidate.content.parts:
                if part.text:
                    return part.text
            return "Sorry, I couldn't generate a response."


# ── Telegram ──────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action("typing")
    try:
        reply = await ask_gemini(update.message.text)
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

#!/usr/bin/env python3
"""
Telegram bot — ask questions about your Strava training and Yazio nutrition.
Claude answers using live data fetched via tool use.

Required environment variables:
    TELEGRAM_BOT_TOKEN      From @BotFather
    ANTHROPIC_API_KEY       Your Anthropic API key
    YAZIO_EMAIL             Your Yazio account email
    YAZIO_PASSWORD          Your Yazio account password
"""

import os
import json
import logging
import requests
from datetime import date, timedelta

import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

# ── Yazio ─────────────────────────────────────────────────────────────────────

_YAZIO_BASE     = "https://yzapi.yazio.com"
_YAZIO_AUTH_URL = f"{_YAZIO_BASE}/v12/oauth/token"
_YAZIO_API_URL  = f"{_YAZIO_BASE}/v15"
_YAZIO_CLIENT_ID     = os.environ.get("YAZIO_CLIENT_ID",     "1_4hiybetvfksgw40o0sog4s884kwc840wwso8go4k8c04goo4c")
_YAZIO_CLIENT_SECRET = os.environ.get("YAZIO_CLIENT_SECRET", "6rok2m65xuskgkgogw40wkkk8sw0osg84s8cggsc4woos4s8o")
_MEAL_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}

_yazio_session: requests.Session | None = None
_product_cache: dict = {}


def _yazio_session_get() -> requests.Session:
    global _yazio_session
    if _yazio_session is None:
        resp = requests.post(_YAZIO_AUTH_URL, json={
            "client_id":     _YAZIO_CLIENT_ID,
            "client_secret": _YAZIO_CLIENT_SECRET,
            "username":      os.environ["YAZIO_EMAIL"],
            "password":      os.environ["YAZIO_PASSWORD"],
            "grant_type":    "password",
        }, timeout=15)
        resp.raise_for_status()
        token = resp.json()["access_token"]
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        _yazio_session = s
    return _yazio_session


def _yazio_product(product_id: str) -> dict:
    if product_id not in _product_cache:
        r = _yazio_session_get().get(f"{_YAZIO_API_URL}/products/{product_id}", timeout=15)
        r.raise_for_status()
        _product_cache[product_id] = r.json()
    return _product_cache[product_id]


def _yazio_recipe(recipe_id: str) -> dict:
    key = f"recipe_{recipe_id}"
    if key not in _product_cache:
        r = _yazio_session_get().get(f"{_YAZIO_API_URL}/recipes/{recipe_id}", timeout=15)
        r.raise_for_status()
        _product_cache[key] = r.json()
    return _product_cache[key]


def _yazio_day(d: date) -> list[dict]:
    resp = _yazio_session_get().get(
        f"{_YAZIO_API_URL}/user/consumed-items",
        params={"date": d.isoformat()}, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data.get("products", []):
        amount  = float(item.get("amount", 0))
        product = _yazio_product(item["product_id"])
        n = product.get("nutrients", {})
        rows.append({
            "date": d.isoformat(), "meal": item.get("daytime", "unknown"),
            "food": product.get("name", item["product_id"]),
            "kcal": round(n.get("energy.energy", 0) * amount, 1),
            "carbs_g": round(n.get("nutrient.carb", 0) * amount, 1),
            "protein_g": round(n.get("nutrient.protein", 0) * amount, 1),
            "fat_g": round(n.get("nutrient.fat", 0) * amount, 1),
        })
    for item in data.get("recipe_portions", []):
        count  = float(item.get("portion_count", 1))
        recipe = _yazio_recipe(item["recipe_id"])
        n = recipe.get("nutrients", {})
        rows.append({
            "date": d.isoformat(), "meal": item.get("daytime", "unknown"),
            "food": f"{recipe.get('name', item['recipe_id'])} (recipe)",
            "kcal": round(n.get("energy.energy", 0) * count, 1),
            "carbs_g": round(n.get("nutrient.carb", 0) * count, 1),
            "protein_g": round(n.get("nutrient.protein", 0) * count, 1),
            "fat_g": round(n.get("nutrient.fat", 0) * count, 1),
        })
    rows.sort(key=lambda r: _MEAL_ORDER.get(r["meal"], 99))
    return rows


def yazio_today() -> list[dict]:
    return _yazio_day(date.today())


def yazio_date(date_str: str) -> list[dict]:
    return _yazio_day(date.fromisoformat(date_str))


def yazio_summary(days: int = 7) -> list[dict]:
    today = date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        rows = _yazio_day(d)
        out.append({
            "date":      d.isoformat(),
            "kcal":      round(sum(r["kcal"]      for r in rows), 1),
            "carbs_g":   round(sum(r["carbs_g"]   for r in rows), 1),
            "protein_g": round(sum(r["protein_g"] for r in rows), 1),
            "fat_g":     round(sum(r["fat_g"]     for r in rows), 1),
            "items":     len(rows),
        })
    return out


# ── Tool definitions for Claude ───────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_today_meals",
        "description": "Get all meals logged in Yazio for today.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_meals_for_date",
        "description": "Get all meals logged in Yazio for a specific date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"}
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_nutrition_summary",
        "description": "Get a daily nutrition summary (calories and macros) for the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of past days (default 7)"}
            },
            "required": [],
        },
    },
]


def dispatch_tool(name: str, inputs: dict) -> str:
    try:
        if name == "get_today_meals":
            return json.dumps(yazio_today())
        elif name == "get_meals_for_date":
            return json.dumps(yazio_date(inputs["date"]))
        elif name == "get_nutrition_summary":
            return json.dumps(yazio_summary(inputs.get("days", 7)))
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Claude ────────────────────────────────────────────────────────────────────

_anthropic = None


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic

SYSTEM_PROMPT = """You are a personal fitness and nutrition assistant.
You have access to the user's Strava training data and Yazio nutrition logs.
Answer questions concisely. Use tools to fetch data when needed.
Today's date is {today}."""


def ask_claude(user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]
    system = SYSTEM_PROMPT.format(today=date.today().isoformat())

    while True:
        response = _get_anthropic().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            # Add assistant's response to messages
            messages.append({"role": "assistant", "content": response.content})
            # Process all tool calls
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            # Final text response
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "Sorry, I couldn't generate a response."


# ── Telegram ──────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.chat.send_action("typing")
    try:
        reply = ask_claude(user_text)
    except Exception as e:
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

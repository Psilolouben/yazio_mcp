#!/usr/bin/env python3
"""
Telegram bot — fetches Yazio data from the MCP server and answers via Groq.

Required environment variables:
    TELEGRAM_BOT_TOKEN      From @BotFather
    GROQ_API_KEY            From console.groq.com
"""

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


# ── Date extraction ───────────────────────────────────────────────────────────

CLASSIFY_PROMPT = """Classify the user's nutrition question. Today is {today}.

Reply with EXACTLY one token:
- TODAY                            → asking about today's food log
- YESTERDAY                        → asking about yesterday's food log
- DATE:YYYY-MM-DD                  → asking about a specific logged date
- RANGE:YYYY-MM-DD:YYYY-MM-DD      → asking about a logged date range
- SCHEDULE                         → asking about the diet plan / schedule (what to eat, meal options, recipes, "what should I have for dinner/lunch/breakfast", "what are my options", anything about the plan)
- UNCLEAR                          → genuinely cannot determine

No explanation, just the token."""


_SCHEDULE_KEYWORDS = [
    # English
    "schedule", "plan", "option", "propose", "suggest", "recommend",
    "what should i", "what to eat", "what can i eat", "what do i eat",
    "for dinner", "for lunch", "for breakfast", "dinner tonight", "lunch today",
    "recipe",
    # Greek
    "πλάνο", "πρόγραμμα", "επιλογή", "πρωινό", "μεσημεριανό", "βραδινό",
    "τι να φάω", "τι να τρώω", "τι έχω", "συνταγή", "πρόταση",
]


async def _classify(user_message: str) -> str:
    msg = user_message.lower()
    if any(kw in msg for kw in _SCHEDULE_KEYWORDS):
        return "SCHEDULE"

    today = date.today().isoformat()
    response = await _get_groq().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": CLASSIFY_PROMPT.format(today=today)},
            {"role": "user",   "content": user_message},
        ],
        max_tokens=25,
    )
    return response.choices[0].message.content.strip()


async def _fetch_for_token(token: str) -> str:
    today = date.today()

    if token == "TODAY":
        data = await _call_tool("get_meals_for_date", {"date": today.isoformat()})
        return f"Meals for {today.isoformat()}:\n{data}"

    if token == "YESTERDAY":
        yesterday = (today - timedelta(days=1)).isoformat()
        data = await _call_tool("get_meals_for_date", {"date": yesterday})
        return f"Meals for {yesterday}:\n{data}"

    if token.startswith("DATE:"):
        d = token[5:]
        data = await _call_tool("get_meals_for_date", {"date": d})
        return f"Meals for {d}:\n{data}"

    if token.startswith("RANGE:"):
        _, start, end = token.split(":")
        data = await _call_tool("get_meals_for_range", {"start_date": start, "end_date": end})
        summary = await _call_tool("get_daily_summary", {"days": (date.fromisoformat(end) - date.fromisoformat(start)).days + 1})
        return f"Meals from {start} to {end}:\n{data}\n\nDaily summaries:\n{summary}"

    if token == "SCHEDULE":
        data = await _call_tool("get_diet_schedule", {})
        return f"Diet schedule data:\n{data}"

    return ""


# ── Answer ────────────────────────────────────────────────────────────────────

FOOD_LOG_PROMPT = """You are a personal nutrition assistant.
Answer the user's question using only the data below. Be concise.
Always reply in the same language the user wrote in.
Today's date is {today}.

--- NUTRITION DATA ---
{data}
---------------------"""

SCHEDULE_PROMPT = """You are a personal nutrition assistant helping with a structured diet plan.

## Κανόνας αντιστοίχισης επιλογών
Οι επιλογές είναι ΖΕΥΓΑΡΩΤΕΣ: αν ο χρήστης έφαγε από Επιλογή 1 σε ένα γεύμα,
ΟΛΑ τα γεύματα της ημέρας είναι από Επιλογή 1.

## Κατηγορίες τροφίμων (για αναγνώριση επιλογής)
Όσπρια: ρεβύθια, φακές, γίγαντες, μαυρομάτικα, αρακάς, ρεβιβάδα, φασόλια
  ⚠️  ρεβύθια (plain / σαλάτα / βραστά) ≠ ρεβιθόπιτα (ψητή πίτα) — διαφορετικά πιάτα
Κρέας / Πουλερικά: κοτόπουλο (ψητό, καλαμάκι, μακαρονοσαλάτα), μοσχάρι, αρνί
Ψάρι / Θαλασσινά: σολομός, λαβράκι, τόνος, γαρίδες
Αυγά: αυγά βραστά, shakshuka, ομελέτα, αυγά μάτια
Ζυμαρικά / Δημητριακά: μακαρόνια, ζυμαρικά, ρύζι, ριζότο, τραχανάς
Λαδερά: αρακάς λαδερός, φασολάκια, μπάμιες, κολοκύθα, μελιτζάνες
Σούπες: κολοκυθόσουπα, τραχανόσουπα, φακές σούπα, κρεμμυδόσουπα

## Οδηγίες απάντησης
1. Βρες ποια Επιλογή αντιστοιχεί στο φαγητό του χρήστη (χρησιμοποίησε κατηγορίες αν δεν υπάρχει ακριβής αντιστοιχία).
2. Απάντησε ΑΜΕΣΑ και ΣΥΝΟΠΤΙΚΑ — μόνο το αποτέλεσμα, χωρίς να εξηγείς τη λογική σου.
3. Μη γράφεις "σύμφωνα με τον κανόνα" ή "δεδομένου ότι" — απλά πες τι να φάει.
4. Αν χρειάζεται διευκρίνιση (π.χ. κοτόπουλο σε πολλές επιλογές), ρώτα με μία μόνο ερώτηση.
5. Απάντησε ΠΑΝΤΑ στη γλώσσα που έγραψε ο χρήστης.
Today's date is {today}.

--- DIET SCHEDULE ---
{data}
--------------------"""


async def ask_groq(user_message: str) -> str:
    token = await _classify(user_message)
    logging.info("Classification token: %s", token)

    if token == "UNCLEAR":
        return "I'm not sure what you're asking about. Try asking about a specific date (e.g. 'what did I eat yesterday?') or your diet plan (e.g. 'what should I have for dinner?')."

    data = await _fetch_for_token(token)
    prompt = SCHEDULE_PROMPT if token == "SCHEDULE" else FOOD_LOG_PROMPT
    max_tok = 250 if token == "SCHEDULE" else None

    response = await _get_groq().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": prompt.format(today=date.today().isoformat(), data=data)},
            {"role": "user",   "content": user_message},
        ],
        **({"max_tokens": max_tok} if max_tok else {}),
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

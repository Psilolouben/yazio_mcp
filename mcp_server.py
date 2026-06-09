#!/usr/bin/env python3
"""
YAZIO MCP Server — HTTP/SSE transport for remote/cloud hosting.

Required environment variables:
    YAZIO_EMAIL      Your YAZIO account email
    YAZIO_PASSWORD   Your YAZIO account password

Optional:
    PORT                  Port to listen on (Render sets this automatically)
    YAZIO_CLIENT_ID       Override the default app client ID
    YAZIO_CLIENT_SECRET   Override the default app client secret

Run locally:
    YAZIO_EMAIL=you@example.com YAZIO_PASSWORD=secret python mcp_server.py

Add to Claude Desktop (remote):
    {
      "mcpServers": {
        "yazio": {
          "url": "https://<your-render-url>/sse"
        }
      }
    }
"""

import os
import sys
from datetime import date, timedelta

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.exit("Missing dependency: pip install mcp")

# ── YAZIO API constants ───────────────────────────────────────────────────────

BASE_URL      = "https://yzapi.yazio.com"
AUTH_URL      = f"{BASE_URL}/v12/oauth/token"
API_URL       = f"{BASE_URL}/v15"
CLIENT_ID     = os.environ.get("YAZIO_CLIENT_ID",     "1_4hiybetvfksgw40o0sog4s884kwc840wwso8go4k8c04goo4c")
CLIENT_SECRET = os.environ.get("YAZIO_CLIENT_SECRET", "6rok2m65xuskgkgogw40wkkk8sw0osg84s8cggsc4woos4s8o")
MEAL_ORDER    = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}

# ── Auth state (lazy, auto-loaded from env) ───────────────────────────────────

_session: requests.Session | None = None
_product_cache: dict = {}


def _authenticate(email: str, password: str) -> requests.Session:
    resp = requests.post(AUTH_URL, json={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username":      email,
        "password":      password,
        "grant_type":    "password",
    }, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"YAZIO authentication failed ({resp.status_code}): {resp.text}")
    token = resp.json()["access_token"]
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    return s


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        email    = os.environ.get("YAZIO_EMAIL", "")
        password = os.environ.get("YAZIO_PASSWORD", "")
        if not email or not password:
            raise RuntimeError("YAZIO_EMAIL and YAZIO_PASSWORD environment variables are required.")
        _session = _authenticate(email, password)
    return _session


# ── Low-level fetchers ────────────────────────────────────────────────────────

def _consumed_items(day: date) -> dict:
    resp = _get_session().get(f"{API_URL}/user/consumed-items", params={"date": day.isoformat()}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _product(product_id: str) -> dict:
    if product_id not in _product_cache:
        resp = _get_session().get(f"{API_URL}/products/{product_id}", timeout=15)
        resp.raise_for_status()
        _product_cache[product_id] = resp.json()
    return _product_cache[product_id]


def _recipe(recipe_id: str) -> dict:
    key = f"recipe_{recipe_id}"
    if key not in _product_cache:
        resp = _get_session().get(f"{API_URL}/recipes/{recipe_id}", timeout=15)
        resp.raise_for_status()
        _product_cache[key] = resp.json()
    return _product_cache[key]


def _process_day(day: date) -> list[dict]:
    """Return a list of meal-item dicts for the given day."""
    data = _consumed_items(day)
    rows = []

    for item in data.get("products", []):
        pid    = item["product_id"]
        amount = float(item.get("amount", 0))
        product = _product(pid)
        n = product.get("nutrients", {})
        rows.append({
            "date":          day.isoformat(),
            "meal":          item.get("daytime", "unknown"),
            "food_name":     product.get("name", pid),
            "amount_g":      amount,
            "calories_kcal": round(n.get("energy.energy", 0) * amount, 1),
            "carbs_g":       round(n.get("nutrient.carb",  0) * amount, 1),
            "protein_g":     round(n.get("nutrient.protein", 0) * amount, 1),
            "fat_g":         round(n.get("nutrient.fat",   0) * amount, 1),
            "notes":         item.get("note", ""),
        })

    for item in data.get("recipe_portions", []):
        rid   = item["recipe_id"]
        count = float(item.get("portion_count", 1))
        recipe = _recipe(rid)
        n = recipe.get("nutrients", {})
        rows.append({
            "date":          day.isoformat(),
            "meal":          item.get("daytime", "unknown"),
            "food_name":     f"{recipe.get('name', rid)} (recipe)",
            "amount_g":      None,
            "calories_kcal": round(n.get("energy.energy", 0) * count, 1),
            "carbs_g":       round(n.get("nutrient.carb",  0) * count, 1),
            "protein_g":     round(n.get("nutrient.protein", 0) * count, 1),
            "fat_g":         round(n.get("nutrient.fat",   0) * count, 1),
            "notes":         item.get("note", ""),
        })

    rows.sort(key=lambda r: MEAL_ORDER.get(r["meal"], 99))
    return rows


# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP("yazio")


@mcp.tool()
def get_meals_for_date(date: str) -> list[dict]:
    """
    Return every meal item logged in YAZIO for a specific date.

    Args:
        date: Date in YYYY-MM-DD format (e.g. "2026-06-09"). Defaults to today if omitted.

    Returns a list of items, each with: date, meal (breakfast/lunch/dinner/snack),
    food_name, amount_g, calories_kcal, carbs_g, protein_g, fat_g, notes.
    """
    from datetime import date as date_type
    d = date_type.fromisoformat(date) if date else date_type.today()
    return _process_day(d)


@mcp.tool()
def get_meals_for_range(start_date: str, end_date: str) -> list[dict]:
    """
    Return all meal items logged in YAZIO between two dates (inclusive).

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date:   End date in YYYY-MM-DD format.

    Returns a flat list of items sorted by date then meal time, each with:
    date, meal, food_name, amount_g, calories_kcal, carbs_g, protein_g, fat_g, notes.
    """
    from datetime import date as date_type
    start = date_type.fromisoformat(start_date)
    end   = date_type.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be >= start_date")

    all_rows = []
    current = start
    while current <= end:
        all_rows.extend(_process_day(current))
        current += timedelta(days=1)
    return all_rows


@mcp.tool()
def get_daily_summary(days: int = 7) -> list[dict]:
    """
    Return a per-day nutrition summary (total calories and macros) for the last N days.

    Args:
        days: Number of past days to summarise, including today (default 7).

    Returns a list of daily totals, each with:
    date, total_calories_kcal, total_carbs_g, total_protein_g, total_fat_g, item_count.
    """
    today = date.today()
    summaries = []

    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        rows = _process_day(d)
        summaries.append({
            "date":                  d.isoformat(),
            "total_calories_kcal":   round(sum(r["calories_kcal"] for r in rows), 1),
            "total_carbs_g":         round(sum(r["carbs_g"]       for r in rows), 1),
            "total_protein_g":       round(sum(r["protein_g"]     for r in rows), 1),
            "total_fat_g":           round(sum(r["fat_g"]         for r in rows), 1),
            "item_count":            len(rows),
        })

    return summaries


@mcp.tool()
def get_today_meals() -> list[dict]:
    """
    Return every meal item logged in YAZIO for today.

    Returns a list of items with: meal, food_name, amount_g, calories_kcal,
    carbs_g, protein_g, fat_g, notes.
    """
    return _process_day(date.today())


if __name__ == "__main__":
    if os.environ.get("RENDER"):
        # Hosted on Render — SSE/HTTP mode
        import uvicorn
        import asyncio
        port = int(os.environ.get("PORT", 8000))
        config = uvicorn.Config(mcp.sse_app(), host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())
    else:
        # Local — stdio mode for Claude Desktop / Claude Code
        mcp.run()

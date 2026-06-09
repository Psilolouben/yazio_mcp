#!/usr/bin/env python3
"""
YAZIO meal & calorie exporter — last 14 days → CSV

Usage:
    python export.py --email you@example.com --password secret
    python export.py --email you@example.com --password secret --days 14 --output meals.csv

Alternatively set env vars:  YAZIO_EMAIL  YAZIO_PASSWORD
"""

import argparse
import csv
import json
import os
import sys
from datetime import date, timedelta
from functools import lru_cache

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

BASE_URL = "https://yzapi.yazio.com"
AUTH_URL = f"{BASE_URL}/v12/oauth/token"
API_URL  = f"{BASE_URL}/v15"

CLIENT_ID     = "1_4hiybetvfksgw40o0sog4s884kwc840wwso8go4k8c04goo4c"
CLIENT_SECRET = "6rok2m65xuskgkgogw40wkkk8sw0osg84s8cggsc4woos4s8o"

CSV_COLUMNS = [
    "date", "meal", "food_name",
    "amount_g", "calories_kcal", "carbs_g", "protein_g", "fat_g",
    "daily_total_kcal", "notes",
]


def authenticate(email: str, password: str) -> str:
    resp = requests.post(AUTH_URL, json={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username":      email,
        "password":      password,
        "grant_type":    "password",
    }, timeout=15)
    if resp.status_code != 200:
        sys.exit(f"Authentication failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    return s


def get_consumed_items(session: requests.Session, day: date) -> dict:
    resp = session.get(f"{API_URL}/user/consumed-items", params={"date": day.isoformat()}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_product(session: requests.Session, product_id: str) -> dict:
    resp = session.get(f"{API_URL}/products/{product_id}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_recipe(session: requests.Session, recipe_id: str) -> dict:
    resp = session.get(f"{API_URL}/recipes/{recipe_id}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def nutrients_for_amount(nutrients: dict, amount_g: float) -> tuple[float, float, float, float]:
    """Return (calories, carbs, protein, fat) for the given gram amount."""
    factor = amount_g
    calories = round(nutrients.get("energy.energy", 0) * factor, 1)
    carbs    = round(nutrients.get("nutrient.carb",  0) * factor, 1)
    protein  = round(nutrients.get("nutrient.protein", 0) * factor, 1)
    fat      = round(nutrients.get("nutrient.fat",   0) * factor, 1)
    return calories, carbs, protein, fat


def process_day(session: requests.Session, day: date, product_cache: dict, debug: bool = False) -> list[dict]:
    data = get_consumed_items(session, day)
    rows = []

    if debug:
        print("\n--- RAW API RESPONSE (first day) ---")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print("------------------------------------\n")

    # Regular products
    for item in data.get("products", []):
        pid    = item["product_id"]
        amount = float(item.get("amount", 0))
        meal   = item.get("daytime", "unknown")

        if pid not in product_cache:
            product_cache[pid] = get_product(session, pid)
        product  = product_cache[pid]
        name     = product.get("name", pid)
        nutrients = product.get("nutrients", {})

        calories, carbs, protein, fat = nutrients_for_amount(nutrients, amount)
        rows.append({
            "date":          day.isoformat(),
            "meal":          meal,
            "food_name":     name,
            "amount_g":      amount,
            "calories_kcal": calories,
            "carbs_g":       carbs,
            "protein_g":     protein,
            "fat_g":         fat,
            "notes":         item.get("note", ""),
        })

    # Recipes / meal plans
    for item in data.get("recipe_portions", []):
        rid    = item["recipe_id"]
        count  = float(item.get("portion_count", 1))
        meal   = item.get("daytime", "unknown")

        if f"recipe_{rid}" not in product_cache:
            product_cache[f"recipe_{rid}"] = get_recipe(session, rid)
        recipe    = product_cache[f"recipe_{rid}"]
        name      = recipe.get("name", rid)
        nutrients = recipe.get("nutrients", {})

        # Recipe nutrients are already per-portion totals when portion_count == 1
        calories = round(nutrients.get("energy.energy", 0) * count, 1)
        carbs    = round(nutrients.get("nutrient.carb",  0) * count, 1)
        protein  = round(nutrients.get("nutrient.protein", 0) * count, 1)
        fat      = round(nutrients.get("nutrient.fat",   0) * count, 1)

        rows.append({
            "date":          day.isoformat(),
            "meal":          meal,
            "food_name":     f"{name} (recipe)",
            "amount_g":      "",
            "calories_kcal": calories,
            "carbs_g":       carbs,
            "protein_g":     protein,
            "fat_g":         fat,
            "notes":         item.get("note", ""),
        })

    return rows


MEAL_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}


def write_csv(rows: list[dict], path: str) -> None:
    # Group by date then meal
    rows = sorted(rows, key=lambda r: (r["date"], MEAL_ORDER.get(r["meal"], 99)))

    # Compute daily totals
    daily_totals: dict[str, float] = {}
    for row in rows:
        d = row["date"]
        daily_totals[d] = daily_totals.get(d, 0) + (row["calories_kcal"] or 0)

    # Mark the first row of each day with the daily total
    seen: set[str] = set()
    for row in rows:
        d = row["date"]
        row["daily_total_kcal"] = round(daily_totals[d], 1) if d not in seen else ""
        seen.add(d)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Export YAZIO meals to CSV")
    parser.add_argument("--email",    default=os.getenv("YAZIO_EMAIL"))
    parser.add_argument("--password", default=os.getenv("YAZIO_PASSWORD"))
    parser.add_argument("--days",     type=int, default=14, help="Number of past days to export (default: 14)")
    parser.add_argument("--output",   default="yazio_export.csv", help="Output CSV file path")
    parser.add_argument("--debug",    action="store_true", help="Print raw API response for the first day and exit")
    args = parser.parse_args()

    if not args.email or not args.password:
        sys.exit("Provide --email / --password or set YAZIO_EMAIL / YAZIO_PASSWORD env vars.")

    print(f"Authenticating as {args.email}...")
    token   = authenticate(args.email, args.password)
    session = make_session(token)

    today  = date.today()
    dates  = [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]

    all_rows      = []
    product_cache = {}

    if args.debug:
        dates = [today]

    for day in dates:
        print(f"  Fetching {day}...", end=" ", flush=True)
        rows = process_day(session, day, product_cache, debug=args.debug)
        print(f"{len(rows)} items")
        all_rows.extend(rows)

    write_csv(all_rows, args.output)
    print(f"\nExported {len(all_rows)} entries to {args.output}")


if __name__ == "__main__":
    main()

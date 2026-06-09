#!/usr/bin/env python3
"""
Probe common YAZIO endpoint names to find meal comments/notes.

Usage:
    python probe_endpoints.py --email you@example.com --password secret
"""

import json
import os
import sys
from datetime import date

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

BASE_URL      = "https://yzapi.yazio.com"
AUTH_URL      = f"{BASE_URL}/v12/oauth/token"
API_URL       = f"{BASE_URL}/v15"
CLIENT_ID     = "1_4hiybetvfksgw40o0sog4s884kwc840wwso8go4k8c04goo4c"
CLIENT_SECRET = "6rok2m65xuskgkgogw40wkkk8sw0osg84s8cggsc4woos4s8o"

TODAY = date.today().isoformat()

CANDIDATES = [
    f"/user/notes",
    f"/user/notes?date={TODAY}",
    f"/user/diary",
    f"/user/diary?date={TODAY}",
    f"/user/journal",
    f"/user/journal?date={TODAY}",
    f"/user/comments",
    f"/user/comments?date={TODAY}",
    f"/user/meal-notes",
    f"/user/meal-notes?date={TODAY}",
    f"/user/meals",
    f"/user/meals?date={TODAY}",
    f"/user/food-diary",
    f"/user/food-diary?date={TODAY}",
    f"/user/logs",
    f"/user/logs?date={TODAY}",
    f"/user/tracking",
    f"/user/tracking?date={TODAY}",
    f"/user/activity",
    f"/user/activity?date={TODAY}",
    f"/notes",
    f"/notes?date={TODAY}",
    f"/diary",
    f"/diary?date={TODAY}",
]


def authenticate(email, password):
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


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--email",    default=os.getenv("YAZIO_EMAIL"))
    parser.add_argument("--password", default=os.getenv("YAZIO_PASSWORD"))
    args = parser.parse_args()

    if not args.email or not args.password:
        sys.exit("Provide --email / --password or set YAZIO_EMAIL / YAZIO_PASSWORD")

    print(f"Authenticating as {args.email}...")
    token = authenticate(args.email, args.password)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })

    print(f"\nProbing {len(CANDIDATES)} endpoints...\n")
    for path in CANDIDATES:
        url = API_URL + path
        try:
            resp = session.get(url, timeout=10)
            status = resp.status_code
            if status == 200:
                body = resp.json()
                print(f"[200 OK]  {path}")
                print(json.dumps(body, indent=2, ensure_ascii=False)[:500])
                print()
            elif status == 404:
                print(f"[404]     {path}")
            else:
                print(f"[{status}]  {path}  —  {resp.text[:100]}")
        except Exception as e:
            print(f"[ERR]     {path}  —  {e}")


if __name__ == "__main__":
    main()

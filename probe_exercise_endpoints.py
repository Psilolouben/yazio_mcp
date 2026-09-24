#!/usr/bin/env python3
"""
Probe candidate YAZIO endpoints for exercise/training/activity data
(the kind that syncs in from Apple Health).

Usage:
    python probe_exercise_endpoints.py --email you@example.com --password secret --date 2026-09-24

Prints any endpoint that returns 200 OK along with a preview of the JSON body,
so we can see the real field names and pick the right one for a new MCP tool.
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
    parser.add_argument("--date",     default=date.today().isoformat(),
                         help="A date (YYYY-MM-DD) you know has a logged workout, e.g. a gym day or walk")
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

    d = args.date

    candidates = [
        f"/user/exercises?date={d}",
        f"/user/exercises",
        f"/user/trainings?date={d}",
        f"/user/training?date={d}",
        f"/user/activities?date={d}",
        f"/user/activity?date={d}",
        f"/user/sports?date={d}",
        f"/user/sport-sessions?date={d}",
        f"/user/workouts?date={d}",
        f"/user/burned-calories?date={d}",
        f"/user/energy-expenditure?date={d}",
        f"/user/health-data?date={d}",
        f"/user/steps?date={d}",
        # Also re-check consumed-items in case exercise entries ride along in the same payload
        f"/user/consumed-items?date={d}",
    ]

    print(f"\nProbing {len(candidates)} endpoints for date {d}...\n")
    for path in candidates:
        url = API_URL + path
        try:
            resp = session.get(url, timeout=10)
            status = resp.status_code
            if status == 200:
                body = resp.json()
                print(f"[200 OK]  {path}")
                print(json.dumps(body, indent=2, ensure_ascii=False)[:1500])
                print()
            elif status == 404:
                print(f"[404]     {path}")
            else:
                print(f"[{status}]  {path}  —  {resp.text[:150]}")
        except Exception as e:
            print(f"[ERR]     {path}  —  {e}")


if __name__ == "__main__":
    main()

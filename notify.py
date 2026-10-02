#!/usr/bin/env python3
"""Send a Discord message if any event in events.json starts within the next N days.

Sends nothing (and exits 0) when there are no upcoming events.
The webhook URL is read from the DISCORD_WEBHOOK_URL environment variable.

    python notify.py                      # real run
    python notify.py --dry-run            # print the message instead of sending
    python notify.py --today 2026-10-02   # pretend today is another date
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DISCORD_LIMIT = 2000  # max characters in a webhook message


def load_events(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    events = []
    for item in raw:
        events.append(
            {
                "activity": item["activity"],
                "section": item.get("section", ""),
                "note": item.get("note", ""),
                "start": date.fromisoformat(item["start"]),
                "end": date.fromisoformat(item["end"]),
            }
        )
    return events


def in_window(events, today, days):
    last = today + timedelta(days=days)
    return sorted(
        (e for e in events if today <= e["start"] <= last),
        key=lambda e: (e["start"], e["end"]),
    )


def fmt_date(d):
    return d.strftime("%a %d/%m")


def when(start, today):
    n = (start - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def build_message(events, today, days):
    lines = [f"📅 **Fechas importantes en los proximos {days} dias**", ""]
    for e in events:
        span = fmt_date(e["start"])
        if e["end"] != e["start"]:
            span += f" → {fmt_date(e['end'])}"
        line = f"• **{span}** ({when(e['start'], today)}): {e['activity']}"
        if e["section"]:
            line += f" _[{e['section']}]_"
        if e["note"]:
            line += f" ({e['note']})"
        lines.append(line)
    msg = "\n".join(lines)
    if len(msg) > DISCORD_LIMIT:
        msg = msg[: DISCORD_LIMIT - 20].rsplit("\n", 1)[0] + "\n… (truncated)"
    return msg


def send_discord(url, content):
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        # Discord (behind Cloudflare) rejects the default Python-urllib User-Agent.
        headers={"Content-Type": "application/json", "User-Agent": "unlam-calendar-notifier/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="events.json")
    ap.add_argument("--days", type=int, default=5, help="look-ahead window in days (default 5)")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now(TZ).date()
    events = load_events(args.file)

    if not any(e["end"] >= today for e in events):
        # Not an error for the user's purposes, but worth seeing in the Actions log.
        print(f"WARNING: {args.file} has no events on or after {today}; the calendar file may be outdated.", file=sys.stderr)

    due = in_window(events, today, args.days)
    if not due:
        print(f"{today}: no events starting in the next {args.days} days. Nothing sent.")
        return 0

    message = build_message(due, today, args.days)
    if args.dry_run:
        print(message)
        return 0

    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1
    try:
        status = send_discord(url, message)
    except (urllib.error.URLError, TimeoutError) as exc:
        # Never print the URL: it contains the webhook secret.
        print(f"ERROR: could not send to Discord: {exc.__class__.__name__}: {getattr(exc, 'reason', exc)}", file=sys.stderr)
        return 1
    print(f"{today}: sent {len(due)} event(s) to Discord (HTTP {status}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

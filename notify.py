#!/usr/bin/env python3
"""Remind about events in events.json on Discord, and clean up events that already ended.

Reminders go out when an event STARTS exactly 5, 3 or 1 days from today
(change with --remind-days). Nothing is sent, and the script exits 0, when no event matches.
The webhook URL is read from the DISCORD_WEBHOOK_URL environment variable.

    python notify.py                       # real run
    python notify.py --dry-run             # print the message instead of sending
    python notify.py --remind-days 7,2,0   # different reminder days
    python notify.py --prune-only          # remove events that already ended, send nothing
    python notify.py --today 2026-10-02    # pretend today is another date
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DISCORD_LIMIT = 2000  # max characters in a webhook message


class EventsFileError(ValueError):
    """events.json contains something the script can't understand."""


def parse_date(value, where):
    """Read a YYYY-MM-DD date. Also accepts non-padded forms like 2026-10-3 or 2026/10/3."""
    parts = re.split(r"[-/]", str(value).strip())
    try:
        if len(parts) != 3 or len(parts[0]) != 4:
            raise ValueError
        y, m, d = (int(p) for p in parts)
        return date(y, m, d)
    except ValueError:
        raise EventsFileError(f"{where}: can't read the date {value!r}. Use YYYY-MM-DD, for example 2026-10-03.") from None


def _field(item, name, where):
    try:
        return item[name]
    except (KeyError, TypeError):
        raise EventsFileError(f"{where}: missing the field {name!r}.") from None


def load_events(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    events = []
    for i, item in enumerate(raw, start=1):
        label = item.get("activity", "?") if isinstance(item, dict) else "?"
        where = f"{path}, item #{i} ({label})"
        events.append(
            {
                "activity": _field(item, "activity", where),
                "section": item.get("section", ""),
                "note": item.get("note", ""),
                "start": parse_date(_field(item, "start", where), where),
                "end": parse_date(_field(item, "end", where), where),
            }
        )
    return events


def select_due(events, today, remind_days):
    """Events whose start date is exactly N days away, for N in remind_days."""
    return sorted(
        (e for e in events if (e["start"] - today).days in remind_days),
        key=lambda e: (e["start"], e["end"]),
    )


def prune_file(path, today, dry_run=False):
    """Remove events whose end date is before today. Returns the removed items.

    Works on the raw JSON items so any extra fields you added are preserved.
    The file is only rewritten when something is actually removed.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    keep, removed = [], []
    for i, item in enumerate(raw, start=1):
        label = item.get("activity", "?") if isinstance(item, dict) else "?"
        where = f"{path}, item #{i} ({label})"
        end = parse_date(_field(item, "end", where), where)  # raises before anything is rewritten
        (removed if end < today else keep).append(item)
    if removed and not dry_run:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    return removed


def fmt_date(d):
    return d.strftime("%a %d/%m")


def when(start, today):
    n = (start - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def build_message(events, today):
    lines = ["📅 **UNLaM: upcoming dates**", ""]
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


def parse_days(text):
    try:
        days = {int(part) for part in text.split(",") if part.strip()}
    except ValueError:
        raise argparse.ArgumentTypeError("use comma-separated whole numbers, e.g. 5,3,1")
    if not days or min(days) < 0:
        raise argparse.ArgumentTypeError("need at least one day, and days can't be negative")
    return days


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="events.json")
    ap.add_argument("--remind-days", type=parse_days, default=parse_days("5,3,1"),
                    help="days before an event's start to send a reminder (default 5,3,1)")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending / writing")
    ap.add_argument("--prune-only", action="store_true", help="remove ended events and exit; sends nothing")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now(TZ).date()

    try:
        if args.prune_only:
            removed = prune_file(args.file, today, dry_run=args.dry_run)
            verb = "Would remove" if args.dry_run else "Removed"
            print(f"{today}: {verb} {len(removed)} ended event(s).")
            for item in removed:
                print(f"  - {item['end']}  {item['activity']}")
            return 0
        events = load_events(args.file)
    except EventsFileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: can't read {args.file}: {exc}", file=sys.stderr)
        return 1

    if not any(e["end"] >= today for e in events):
        # Worth seeing in the Actions log: the file needs new dates.
        print(f"WARNING: {args.file} has no events on or after {today}; the calendar file may be outdated.", file=sys.stderr)

    due = select_due(events, today, args.remind_days)
    if not due:
        days_txt = ", ".join(str(d) for d in sorted(args.remind_days, reverse=True))
        print(f"{today}: no event starts {days_txt} days from today. Nothing sent.")
        return 0

    message = build_message(due, today)
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

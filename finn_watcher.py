#!/usr/bin/env python3
"""Poll finn.no Torget searches and notify a Discord webhook about new listings.

Run manually or on a schedule (cron/systemd timer). All behavior is
controlled by config.yaml next to this script — see that file's comments.
"""
import argparse
import base64
import json
import logging
import re
import signal
import sys
import time
from pathlib import Path

import requests
import yaml

SEARCH_URL = "https://www.finn.no/recommerce/forsale/search"
REACT_QUERY_STATE_RE = re.compile(
    r'<script type="application/json" data-react-query-state>(.*?)</script>', re.S
)

log = logging.getLogger("finn_watcher")


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not config.get("discord_webhook_url"):
        raise SystemExit("config.yaml: discord_webhook_url is not set")
    if not config.get("searches"):
        raise SystemExit("config.yaml: no searches configured")
    return config


def load_seen(state_file: Path) -> set:
    if state_file.exists():
        return set(json.loads(state_file.read_text(encoding="utf-8")))
    return set()


def save_seen(state_file: Path, seen: set) -> None:
    state_file.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def fetch_docs(query: str, price_to, user_agent: str) -> list:
    """Fetch a finn.no Torget search and return the raw listing dicts."""
    params = {"q": query, "sort": "PUBLISHED_DESC"}
    if price_to:
        params["price_to"] = price_to
    resp = requests.get(
        SEARCH_URL, params=params, headers={"User-Agent": user_agent}, timeout=20
    )
    resp.raise_for_status()
    return _extract_docs(resp.text)


def _extract_docs(html: str) -> list:
    """finn.no embeds search results as base64-encoded JSON (react-query
    hydration state) in a <script> tag rather than plain HTML, so we pull
    that out instead of scraping DOM elements."""
    match = REACT_QUERY_STATE_RE.search(html)
    if not match:
        log.warning("could not find react-query-state script in response")
        return []
    try:
        data = json.loads(base64.b64decode(match.group(1)))
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("failed to decode react-query-state: %s", exc)
        return []
    for query in data.get("queries", []):
        key = query.get("queryKey") or []
        if key and isinstance(key[0], dict) and key[0].get("scope") == "search":
            return query.get("state", {}).get("data", {}).get("docs", []) or []
    return []


def matches_keywords(heading: str, include: list, exclude: list) -> bool:
    heading = heading.lower()
    if include and not any(k.lower() in heading for k in include):
        return False
    if exclude and any(k.lower() in heading for k in exclude):
        return False
    return True


def notify_discord(webhook_url: str, doc: dict, search_name: str) -> None:
    price = doc.get("price", {}).get("amount")
    title = doc.get("heading") or "(uten tittel)"
    location = doc.get("location") or "ukjent sted"
    url = doc.get("canonical_url")
    image_url = doc.get("image", {}).get("url")

    price_str = f"{price:,} kr".replace(",", " ") if price is not None else "ukjent pris"
    embed = {
        "title": title[:256],
        "url": url,
        "description": f"**{price_str}**\n{location}\nTreff på søk: {search_name}",
        "color": 0x57F287,
    }
    if image_url:
        embed["thumbnail"] = {"url": image_url}

    resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=15)
    resp.raise_for_status()


def check_once(config: dict, seen: set, first_run: bool, dry_run: bool) -> int:
    """Run every configured search once, notify on new matches, and add them
    to `seen` in place. Returns the number of new listings notified."""
    notify_on_first_run = config.get("notify_on_first_run", False)
    user_agent = config.get("user_agent", "finn-watcher/1.0")
    global_max_price = config.get("max_price_kr")
    delay = config.get("request_delay_seconds", 2)

    new_count = 0
    for i, search in enumerate(config["searches"]):
        name = search["name"]
        max_price = search.get("max_price_kr", global_max_price)
        log.debug("checking %r (query=%r, max_price=%s)", name, search["query"], max_price)

        try:
            docs = fetch_docs(search["query"], max_price, user_agent)
        except requests.RequestException as exc:
            log.error("request failed for %r: %s", name, exc)
            continue

        for doc in docs:
            listing_id = doc.get("id")
            if not listing_id or listing_id in seen:
                continue

            price = doc.get("price", {}).get("amount")
            if max_price is not None and (price is None or price > max_price):
                continue

            if not matches_keywords(
                doc.get("heading", ""),
                search.get("include_keywords") or [],
                search.get("exclude_keywords") or [],
            ):
                continue

            seen.add(listing_id)

            if first_run and not notify_on_first_run:
                continue

            new_count += 1
            log.info("new match [%s]: %s (%s kr)", name, doc.get("heading"), price)
            if not dry_run:
                try:
                    notify_discord(config["discord_webhook_url"], doc, name)
                except requests.RequestException as exc:
                    log.error("failed to notify discord: %s", exc)

        if i < len(config["searches"]) - 1:
            time.sleep(delay)

    return new_count


def run(config_path: Path, dry_run: bool = False) -> None:
    """Single pass: check every search once, notify, and exit. Suited to
    being invoked from cron."""
    config = load_config(config_path)
    state_file = config_path.parent / config.get("state_file", "seen_listings.json")
    seen = load_seen(state_file)
    first_run = not seen and not state_file.exists()

    new_count = check_once(config, seen, first_run, dry_run)
    save_seen(state_file, seen)

    if first_run and not config.get("notify_on_first_run", False):
        log.info(
            "first run: recorded %d existing listings as seen, no notifications sent",
            len(seen),
        )
    else:
        log.info("done: %d new listing(s) notified", new_count)


def run_loop(config_path: Path, dry_run: bool = False) -> None:
    """Poll continuously until interrupted, so new listings get notified
    within one poll interval of appearing on finn.no."""
    stop = False

    def handle_signal(signum, _frame):
        nonlocal stop
        log.info("received signal %s, stopping after this pass", signum)
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    config = load_config(config_path)
    state_file = config_path.parent / config.get("state_file", "seen_listings.json")
    seen = load_seen(state_file)
    first_run = not seen and not state_file.exists()
    interval = config.get("poll_interval_seconds", 45)

    log.info("starting loop, polling every %ss (ctrl-c to stop)", interval)

    while not stop:
        # config is reloaded every pass so edits to config.yaml take effect
        # without restarting the process.
        config = load_config(config_path)
        try:
            new_count = check_once(config, seen, first_run, dry_run)
        except Exception:
            log.exception("pass failed, will retry next interval")
            new_count = 0
        save_seen(state_file, seen)

        if first_run and not config.get("notify_on_first_run", False):
            log.info(
                "first run: recorded %d existing listings as seen, no notifications sent",
                len(seen),
            )
        elif new_count:
            log.info("pass done: %d new listing(s) notified", new_count)
        first_run = False

        for _ in range(int(interval)):
            if stop:
                break
            time.sleep(1)

    log.info("stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "config.yaml",
        help="path to config.yaml (default: alongside this script)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log matches but don't actually post to Discord",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="poll continuously (every poll_interval_seconds from config.yaml) "
        "instead of checking once and exiting. Use this for near-instant "
        "notifications; run it as a systemd service or in a long-lived "
        "terminal/tmux session rather than from cron.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        if args.loop:
            run_loop(args.config, dry_run=args.dry_run)
        else:
            run(args.config, dry_run=args.dry_run)
    except Exception:
        log.exception("finn_watcher run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()

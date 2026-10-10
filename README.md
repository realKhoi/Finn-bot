# finn-watcher

Watches [finn.no](https://www.finn.no) Torget searches and posts new listings to a Discord channel via webhook.

Each new listing shows up as an embed with title, price, location, a thumbnail and a link straight to the ad.

## Features

- Multiple saved searches, each with its own name and filters
- Max price per search, or one global limit
- Include/exclude keywords matched against the listing title
- Remembers listings it has already seen, so you only get notified once
- No flood on first run: existing listings are recorded silently
- Runs once (for cron) or as a continuous loop (for systemd)
- Config is reloaded every pass in loop mode, so edits take effect without a restart

## Requirements

- Python 3.8+
- A Discord webhook URL (Channel settings → Integrations → Webhooks → New Webhook)

## Installation

```bash
git clone https://github.com/realKhoi/Finn-bot.git finn-watcher
cd finn-watcher
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Then edit `config.yaml` with your webhook URL and searches.

## Configuration

`config.yaml` lives next to the script. Example:

```yaml
discord_webhook_url: "https://discord.com/api/webhooks/..."

# Optional global settings
max_price_kr: 5000              # default price limit for all searches
poll_interval_seconds: 45       # loop mode only
request_delay_seconds: 2        # pause between searches in a pass
notify_on_first_run: false      # true = notify on everything found on the first run
state_file: seen_listings.json
user_agent: "finn-watcher/1.0"

searches:
  - name: "Gaming monitor"
    query: "skjerm 144hz"
    max_price_kr: 1500          # overrides the global limit
    include_keywords: ["27", "144"]
    exclude_keywords: ["defekt", "ødelagt"]

  - name: "Road bike"
    query: "racersykkel"
```

| Key | Required | Default | Description |
|---|---|---|---|
| `discord_webhook_url` | yes | | Webhook that notifications are posted to |
| `searches` | yes | | List of searches (see below) |
| `max_price_kr` | no | none | Global max price; listings without a price are skipped when set |
| `poll_interval_seconds` | no | `45` | Seconds between passes in `--loop` mode |
| `request_delay_seconds` | no | `2` | Pause between searches, to be polite to finn.no |
| `notify_on_first_run` | no | `false` | Notify on all existing listings the first time it runs |
| `state_file` | no | `seen_listings.json` | Where seen listing IDs are stored |
| `user_agent` | no | `finn-watcher/1.0` | User-Agent header for requests |

For intervals and delay, KEEP IT REASONABLE, I am not responsible if you get IP banned from Finn.no

Each search supports:

| Key | Required | Description |
|---|---|---|
| `name` | yes | Label shown in the Discord message |
| `query` | yes | Search text, same as typing it into finn.no |
| `max_price_kr` | no | Overrides the global max price |
| `include_keywords` | no | Title must contain at least one of these (case-insensitive) |
| `exclude_keywords` | no | Title must contain none of these (case-insensitive) |

> **Keep `config.yaml` out of git.** Your webhook URL lets anyone post to your channel. It's listed in `.gitignore`; commit changes to `config.example.yaml` instead.

## Usage

```bash
python3 finn_watcher.py              # check once and exit
python3 finn_watcher.py --loop       # keep polling until stopped
python3 finn_watcher.py --dry-run    # log matches without posting to Discord
python3 finn_watcher.py -v           # verbose/debug logging
python3 finn_watcher.py --config /path/to/config.yaml
```

### Run on a schedule with cron

Checks every 5 minutes:

```cron
*/5 * * * * cd /root/finn-watcher && /usr/bin/python3 finn_watcher.py >> watcher.log 2>&1
```

### Run as a systemd service (recommended)

Uses `--loop` for near-instant notifications and restarts automatically:

```bash
cp finn-watcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now finn-watcher
```

Check on it:

```bash
systemctl status finn-watcher
journalctl -u finn-watcher -f
```

Check the paths in `finn-watcher.service` match where you cloned the repo.

## How it works

finn.no embeds its search results as base64-encoded JSON inside a `<script data-react-query-state>` tag. The watcher fetches each search (sorted newest first), decodes that data, filters by price and keywords, and posts anything it hasn't seen before.

Seen listing IDs are saved to `seen_listings.json`, which is created automatically.

## Troubleshooting

- **No notifications at all:** run with `--dry-run -v` to see what each search returns. On the very first run nothing is sent by design.
- **"could not find react-query-state script":** finn.no has likely changed its page structure and the parser needs updating.
- **Want to start fresh:** delete `seen_listings.json`. Don't empty it or replace it with `[]`: an empty file crashes the script, and `[]` skips the first-run protection and notifies on everything.
- **Discord errors:** check that the webhook URL is correct and the webhook hasn't been deleted.

## Disclaimer

Not affiliated with finn.no. Keep the poll interval reasonable and respect finn.no's terms of use.
The code is 100% vibecoded using Claude Code, I have only done minor edits to suffice my own requirements.

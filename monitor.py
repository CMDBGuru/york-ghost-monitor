#!/usr/bin/env python3
"""
York Ghost Merchants stock monitor.

Checks the shop + Bowler Hat pages via Squarespace's JSON view and sends an
alert (phone push + email) when a new ghost is listed or an
existing one flips from sold out to in stock.

Runs on a schedule via GitHub Actions. Standard library only -- no pip installs.

State is persisted in state.json (committed back to the repo by the workflow)
so we only alert on *changes*, never re-alert for the same thing.

Alert conditions per page:
  * a product id appears that we've never seen           -> "New listing"
  * a known product flips sold out -> in stock           -> "In stock now"
  * (Bowler Hat only) the page's content hash changes     -> "Page changed"

Safety: a failed fetch NEVER counts as "items disappeared". We carry the last
known state for that page forward, so a network blip can't fire a false alarm.
"""

import os
import sys
import json
import ssl
import time
import smtplib
import hashlib
import urllib.request
from email.message import EmailMessage
from datetime import datetime, timezone

BASE = "https://www.yorkghostmerchants.com"

# name -> path. All four shop pages use item/stock logic. bowlerhat also gets a
# content-hash check because the lottery opening may not appear as a normal item.
PAGES = [
    ("shop",              "/shop"),
    ("rare-and-unusual",  "/shop/rare-and-unusual"),
    ("black-box-edition", "/shop/black-box-edition"),
    ("miscellaneos",      "/shop/miscellaneos"),
    ("bowlerhat",         "/bowlerhat"),
]
HASH_PAGES = {"bowlerhat"}  # pages where a raw content change also counts

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
UA = "Mozilla/5.0 (compatible; ghost-monitor/1.0)"


# --------------------------------------------------------------------------- #
# Fetch + parse
# --------------------------------------------------------------------------- #
def fetch_json(slug, retries=3):
    url = f"{BASE}{slug}?format=json-pretty"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {slug}: {last}")


def item_instock(it):
    for v in (it.get("variants") or []):
        if v.get("unlimited") or (v.get("qtyInStock") or 0) > 0:
            return True
    return False


def price_str(it):
    cents = it.get("priceCents") or 0
    if not cents:
        for v in (it.get("variants") or []):
            cents = v.get("price") or 0
            if cents:
                break
    return f"£{cents/100:.2f}" if cents else ""


def page_state(name, data):
    """Build the comparable state for one page from its JSON."""
    items = {}
    for it in (data.get("items") or []):
        iid = it.get("id")
        if not iid:
            continue
        items[iid] = {
            "title": it.get("title") or "(untitled)",
            "url": it.get("fullUrl") or "",
            "price": price_str(it),
            "inStock": item_instock(it),
        }
    state = {"items": items}
    if name in HASH_PAGES:
        coll = data.get("collection") or {}
        blob = json.dumps(
            [data.get("items") or [], coll.get("mainContent") or "",
             coll.get("items") or []],
            sort_keys=True, default=str,
        )
        state["hash"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return state


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #
def diff_page(name, prev, cur):
    """Return a list of alert dicts for one page."""
    alerts = []
    prev_items = (prev or {}).get("items", {})
    cur_items = cur.get("items", {})

    for iid, info in cur_items.items():
        was = prev_items.get(iid)
        if was is None:
            alerts.append({
                "kind": "IN STOCK" if info["inStock"] else "NEW",
                "page": name, "title": info["title"],
                "price": info["price"], "url": info["url"],
                "inStock": info["inStock"],
            })
        elif info["inStock"] and not was.get("inStock"):
            alerts.append({
                "kind": "IN STOCK", "page": name, "title": info["title"],
                "price": info["price"], "url": info["url"], "inStock": True,
            })

    if name in HASH_PAGES and prev is not None:
        if cur.get("hash") and prev.get("hash") and cur["hash"] != prev["hash"]:
            # Only report as a standalone signal if no item alert already covers it
            if not alerts:
                alerts.append({
                    "kind": "PAGE CHANGED", "page": name,
                    "title": "Bowler Hat page changed (lottery may be open)",
                    "price": "", "url": f"{BASE}/bowlerhat", "inStock": False,
                })
    return alerts


# --------------------------------------------------------------------------- #
# Notify
# --------------------------------------------------------------------------- #
def send_mail(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(user, pw)
        s.send_message(msg)


def send_push(title, body, click_url):
    """Push to ntfy.sh -- a phone alert that doesn't depend on a carrier.

    AT&T discontinued its email-to-SMS gateway, so this is the primary
    phone alert. Priority 'urgent' makes it break through Do Not Disturb.
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title.encode("ascii", "ignore").decode() or "York Ghost",
        "Priority": "urgent",
        "Tags": "ghost",
    }
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        print("  sent push -> ntfy topic")
    except Exception as e:  # noqa: BLE001
        print("  push send FAILED:", e)


def notify(alerts):
    """Send a phone push and a fuller email."""
    email_to = os.environ.get("MAIL_TO_EMAIL", "").strip()

    buyable = [a for a in alerts if a["inStock"]]
    lead = buyable[0] if buyable else alerts[0]
    tag = "IN STOCK" if lead["inStock"] else "NEW"

    # --- Phone push (ntfy): short, with a tappable link straight to the ghost ---
    lead_url = (f"{BASE}{lead['url']}" if lead["url"].startswith("/")
                else lead["url"])
    push_title = ("Ghost IN STOCK!" if lead["inStock"] else "New ghost listed")
    push_body = f"{lead['title']} {lead['price']}".strip()
    if len(alerts) > 1:
        push_body += f"\n(+{len(alerts)-1} more)"
    send_push(push_title, push_body, lead_url)

    # NOTE: carrier email-to-SMS is intentionally gone. AT&T discontinued its
    # gateway, so the phone alert is the ntfy push above. Keeping the old code
    # would also print the phone number into the workflow log in cleartext.

    # --- Email: full detail ---
    lines = ["A change was detected on The York Ghost Merchants shop:\n"]
    for a in alerts:
        url = f"{BASE}{a['url']}" if a["url"].startswith("/") else a["url"]
        lines.append(f"[{a['kind']}] {a['title']} {a['price']}".rstrip())
        lines.append(f"    page: /{a['page']}")
        lines.append(f"    {url}\n")
    lines.append("Go go go -- these sell out fast.")
    email_body = "\n".join(lines)
    subject = f"🔔 York Ghost: {tag} — {lead['title'][:50]}"
    if email_to:
        try:
            send_mail(email_to, subject, email_body)
            print("  sent email ->", email_to)
        except Exception as e:  # noqa: BLE001
            print("  email send FAILED:", e)


def send_test():
    fake = [{"kind": "TEST", "page": "shop", "inStock": True,
             "title": "Test alert — monitor is working",
             "price": "£29.00", "url": "/shop"}]
    notify(fake)
    print("test notification attempted")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def main():
    if os.environ.get("SEND_TEST") == "1" or "--test" in sys.argv:
        send_test()
        return

    saved = load_state()
    prev_pages = saved.get("pages", {})
    first_run = not saved.get("initialized")

    new_pages = dict(prev_pages)  # carry forward by default (blip-safe)
    all_alerts = []

    for name, slug in PAGES:
        try:
            data = fetch_json(slug)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] fetch error, keeping last state: {e}")
            continue  # do NOT overwrite -> no false "disappeared" signal
        cur = page_state(name, data)
        if not first_run:
            all_alerts += diff_page(name, prev_pages.get(name), cur)
        new_pages[name] = cur
        n = len(cur["items"])
        print(f"[{name}] {n} item(s)"
              + (f" hash={cur.get('hash')}" if "hash" in cur else ""))

    state = {
        "initialized": True,
        "updated": datetime.now(timezone.utc).isoformat(),
        "pages": new_pages,
    }
    save_state(state)

    if first_run:
        print("First run: baseline saved, no alerts sent.")
        return

    if all_alerts:
        print(f"{len(all_alerts)} alert(s) -> notifying")
        notify(all_alerts)
    else:
        print("No changes.")


if __name__ == "__main__":
    main()

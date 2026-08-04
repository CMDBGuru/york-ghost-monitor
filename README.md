# York Ghost Merchants — stock monitor

Checks the York Ghost Merchants shop + Bowler Hat pages every ~5 minutes via
GitHub Actions and sends a **phone push notification + email** the moment a new
ghost is listed or an existing one flips from *sold out* to *in stock*.

Pages watched:
- `/shop`
- `/shop/rare-and-unusual`
- `/shop/black-box-edition`
- `/shop/miscellaneos`
- `/bowlerhat` (also alerts on any content change — the lottery opening)

No servers, no cost. Your computer does not need to be on.

## How it works

`monitor.py` reads each page's Squarespace JSON (`?format=json-pretty`),
compares it to the last known state in `state.json`, and alerts on change.
`state.json` is committed back to the repo each time something changes, so you
never get the same alert twice. A failed fetch is ignored (never treated as
"sold out"), so a network blip can't cause a false alarm.

Phone alerts go through [ntfy.sh](https://ntfy.sh) — a free push service that
needs no account. Carrier email-to-SMS gateways are deliberately **not** used:
AT&T discontinued theirs, and the others are unreliable.

## Setup (one time)

Run `setup.ps1` — it installs the GitHub CLI if needed, logs you in, creates
the repo, and sets every secret below. To only re-point notifications, the
secrets can also be set by hand:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret name     | Value                                                    |
|-----------------|----------------------------------------------------------|
| `SMTP_USER`     | the Gmail address that sends the alerts                   |
| `SMTP_PASS`     | a Gmail **App Password** (16 chars, no spaces)            |
| `MAIL_TO_EMAIL` | where the alert email should land                         |
| `NTFY_TOPIC`    | your private ntfy topic name (random string, keep secret) |

### Gmail App Password
Requires 2-Step Verification on the Google account.
1. Go to https://myaccount.google.com/apppasswords
2. Name it "ghost monitor" → **Create**
3. Copy the 16 characters into the `SMTP_PASS` secret.

### Phone push
1. Install **ntfy** (App Store / Google Play) — free, no account.
2. Tap **+**, subscribe to the exact topic in your `NTFY_TOPIC` secret.
3. Anyone who knows the topic name can send you alerts, so keep it private.

### Test it
**Actions → York Ghost Monitor → Run workflow →** tick **send_test → Run**.
A push and an email should arrive within a minute.

## Troubleshooting

- **Nothing arrives:** open the failing run in the **Actions** tab.
  `push send FAILED` / `email send FAILED` lines say which leg broke.
  Email problems are usually a wrong or expired Gmail App Password, or
  2-Step Verification not enabled.
- **Email works, no push:** confirm the ntfy app is subscribed to the exact
  topic string, and that notifications are allowed for the app.
- **Runs are late:** GitHub's free scheduler can delay or skip runs under
  load. That's the trade-off for a free always-on cron.
- **Runs stopped entirely:** if the repo is *private*, Actions minutes are
  capped (2,000/month on the free plan) and 5-minute checks exhaust them in
  about a week. Public repos get unlimited minutes — that's why this one is
  public. It contains no personal data; all credentials live in encrypted
  repository secrets, never in the code.

## Adjusting

- **Check frequency:** edit the `cron` line in
  `.github/workflows/monitor.yml` (`*/5` = every 5 min; GitHub's minimum is 5).
- **Watched pages:** edit the `PAGES` list in `monitor.py`.
- **Stop it:** disable the workflow in the Actions tab, or delete the repo.

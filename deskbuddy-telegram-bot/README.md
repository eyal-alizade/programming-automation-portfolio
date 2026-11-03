# DeskBuddy 🐾

A friendly Hebrew Telegram bot that turns a customer's chat into a structured business request, then gives an authorized operator a small ticket queue.

**Portfolio demonstration by Eyal Alizade / אייל אליזאדה.** The bundled scenario uses synthetic people and requests. This is a working reference project, not client work, an AI assistant, or a claim of production readiness.

## Try it in a minute

Python **3.11 or newer**. No external packages, bot account, token, installation, or network access is needed for the demo.

```bash
python -m deskbuddy demo
python -m deskbuddy demo --json
python -m unittest discover -s tests -v
```

The scripted demo opens a request, restarts mid-conversation, resumes the draft, rejects an unauthorized admin command, ignores a repeated update, and closes the ticket through the admin role. See the generated [synthetic transcript](examples/demo-transcript.txt).

For interactive exploration:

```bash
python -m deskbuddy simulate
```

The simulator begins as synthetic customer `1001`. Enter `:as 9001` to switch to the synthetic admin, `:as 1001` to return to the customer, and `:quit` to exit. These role switches exist only in the offline simulator; a live Telegram sender cannot select an identity this way.

To retain and resume an offline session:

```bash
python -m deskbuddy simulate --db .deskbuddy/offline.sqlite3
```

## What it does

| Customer | Authorized operator |
| --- | --- |
| `/new`: name → request → contact → explicit confirmation | `/queue [page]`: open requests, ten per page |
| `/status [id]`: own ticket or three most recent tickets | `/ticket id`: full request and contact details |
| `/cancel`: discard an unsubmitted draft | `/close id [note]`: close once and notify its requester |
| `/start`, `/help`: short Hebrew guidance | Operator commands require configured numeric sender IDs |

The intake asks for a result, not a payment. Confirmation creates a ticket; it does not create a commercial order or promise a price or deadline. Notifications are limited to the requesting customer and configured operators. There is no contact scraping, broadcast mode, unsolicited outreach, payment flow, or generative AI.

## Architecture

```text
Telegram polling / Offline simulator
                 │
             Message model
                 │
         BotService state machine
                 │
     SQLite transaction: draft + ticket
        + processed update + outbox
                 │
             Runner delivery
                 │
        TelegramAPI / FakeAPI
```

- `service.py`: intake transitions, input bounds, customer ownership and operator authorization.
- `db.py`: SQLite repository, durable drafts, rate counters, tickets, update history and outbox.
- `runner.py`: persisted polling offset, outgoing delivery and transient retry behavior.
- `transport.py`: dependency-free HTTPS adapter and an in-memory offline transport.
- `demo.py`: deterministic scenario and interactive role simulator.

Business state and outgoing replies commit together under `BEGIN IMMEDIATE`. Replaying an already processed `update_id` cannot create a second ticket or enqueue another reply. Pending replies survive a process restart.

**Delivery has an explicit limit:** if Telegram accepts a message and the process stops before recording it as sent, that reply may be sent again after restart. The outbox provides retryable delivery, not impossible end-to-end exactly-once messaging. Permanently rejected replies are marked `failed` with an error code in the outbox; they are not retried automatically.

## Optional live polling

Live polling is the only mode that makes Telegram requests. It was **not** exercised against a real bot for this portfolio release. First configure your own bot and authorized operator IDs using your normal secure credential process.

Required environment variables:

| Variable | Meaning |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Your bot token, supplied only in the runtime environment |
| `DESKBUDDY_ADMIN_IDS` | Comma-separated positive numeric Telegram user IDs, at most twenty |

Then run:

```bash
python -m deskbuddy poll --db .deskbuddy/live.sqlite3
```

`.env.example` documents the variable names; the program does not load `.env` files. Never commit a token. The HTTP adapter deliberately excludes token-bearing URLs and untrusted response bodies from error messages. Outgoing text is sent without Markdown or HTML parsing.

Run one polling process per bot/database. Polling does not work while a webhook is active; this program does not silently remove one. Operators need to start their own chat with the bot before receiving private notifications. Offline databases cannot be reused in live mode, and a live database is bound to one bot ID.

## Bounds and operating limits

- Private, human-authored text messages only; groups, edits and attachments are ignored.
- Twelve processed messages per user per sixty-second window. One warning is sent when the limit is reached; the counter survives restart.
- Name: 2–80 characters. Request: 10–1,000. Contact: 3–120. Incoming text over 2,000 characters is rejected.
- At most three open requests per customer. Closing a request twice does not notify its owner twice.
- SQLite contains contact details in plain text. Retention, deletion/export tools, encrypted storage, monitoring and a deployment service are outside this demo.
- Update/outbox history is retained; plan retention before using this with ongoing real traffic. Failed outbox entries require operator inspection in SQLite.

## Verification

The unittest suite covers ownership, operator authorization, draft cancellation, restart persistence, repeated updates, transaction rollback, rate limits, pending delivery, failed delivery, persisted offsets, offline/live isolation, token redaction and network-free demos. HTTP behavior is verified through an injected fake opener; it is not a live Telegram integration test.

The transport follows the official [Telegram Bot API documentation](https://core.telegram.org/bots/api), checked on 3 November 2026: `getUpdates` offsets, message-only update filtering, `sendMessage` and retry parameters. No optional Telegram SDK is required.

Contact: **Eyal Alizade / אייל אליזאדה** — **mr.pitronot@gmail.com**.

# Deployment Guide

## Server Details

| | |
|---|---|
| **Server IP** | 192.168.1.15 |
| **OS** | Linux (Ubuntu/Debian) |
| **Frappe bench** | ~/frappe-bench |
| **Site** | your-site.local |
| **OpenWA** | ~/OpenWA (git clone) |

---

## Quick Deploy (After Code Changes)

```bash
# 1. Copy changed files to server
scp whatsapp_message.py user@192.168.1.15:~/frappe-bench/apps/openwa_bridge/openwa_bridge/
scp whatsapp_notification.py user@192.168.1.15:~/frappe-bench/apps/openwa_bridge/openwa_bridge/
scp whatsapp_templates.py user@192.168.1.15:~/frappe-bench/apps/openwa_bridge/openwa_bridge/
scp utils.py user@192.168.1.15:~/frappe-bench/apps/openwa_bridge/openwa_bridge/
scp inbound.py user@192.168.1.15:~/frappe-bench/apps/openwa_bridge/openwa_bridge/

# 2. Restart bench
ssh user@192.168.1.15 "cd ~/frappe-bench && bench restart"

# 3. Test: submit a Sales Invoice via POS
```

## Full Git Deploy (Preferred)

```bash
# On server
cd ~/frappe-bench/apps/openwa_bridge

# Discard any local changes (use remote version)
git checkout -- .
git clean -fd openwa_bridge/public/

# Pull latest from GitHub
git pull --no-rebase origin feature/improvements

# Migrate + build + restart
bench migrate --site erp.manaasoft.com
bench build --app openwa_bridge
bench restart

# Enable Frappe scheduler (one-time, for auto-reconnect tasks)
bench --site erp.manaasoft.com scheduler enable
```

**Note**: GitHub PAT may be required for push (password auth disabled). Use `git pull --no-rebase` to avoid merge conflicts.

---

## Initial Setup

### 1. Install OpenWA

```bash
cd ~
git clone https://github.com/rmyndharis/OpenWA.git OpenWA
cd OpenWA
npm install
npm run build
```

### 2. Configure OpenWA Environment

OpenWA uses `.env` files for configuration. Create or edit `~/OpenWA/.env`:

```env
# =============================================================================
# CORE
# =============================================================================
NODE_ENV=production
# Port the app binds to when run directly (bare metal / npm run start:prod).
# In bundled Docker Compose the container always listens on 2785;
# the API_PORT below is only the HOST-published port.
PORT=2785

# Host network interface for bare-metal / systemd. 0.0.0.0 binds to all
# network interfaces, allowing external browser access without relying on
# VS Code SSH port forwarding tunnels. Without this, Node.js defaults to
# 127.0.0.1 — only accessible from inside the server.
HOST=0.0.0.0

# Docker Compose only: host-side port mapped to the container's 2785
# (no effect on bare-metal run).
API_PORT=2785
LOG_LEVEL=info
DOMAIN=localhost
CORS_ORIGINS=http://192.168.1.15
CSP_UPGRADE_INSECURE_REQUESTS=false
AUTO_START_SESSIONS=true

# =============================================================================
# ENGINE
# =============================================================================
ENGINE_TYPE=whatsapp-web.js
SESSION_DATA_PATH=./data/sessions
PUPPETEER_HEADLESS=true
PUPPETEER_ARGS=--no-sandbox,--disable-setuid-sandbox,--disable-dev-shm-usage,--disable-gpu
# PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium  # uncomment if Chrome not found

# =============================================================================
# DATABASE
# =============================================================================
DATABASE_TYPE=sqlite
DATABASE_SYNCHRONIZE=false

# =============================================================================
# SECURITY
# =============================================================================
API_MASTER_KEY=your-strong-secret-key-here

# =============================================================================
# WEBHOOK (SSRF — allow Frappe to reach OpenWA)
# =============================================================================
WEBHOOK_TIMEOUT=10000
WEBHOOK_MAX_RETRIES=3
WEBHOOK_RETRY_DELAY=5000

# Keep global SSRF protection ON; whitelist only your Frappe site:
SSRF_ALLOWED_HOSTS=your-site-name,your-server-ip,localhost,127.0.0.1,minio
# your-site-name = your Frappe/ERPNext site name (e.g., my-site)
# your-server-ip = the IP used to connect from VM to host (e.g., 192.168.1.15)
# minio = MinIO service hostname (if used for file storage)

# =============================================================================
# REDIS (use port 6385 if ERPNext shares this server)
# =============================================================================
REDIS_ENABLED=true
REDIS_HOST=127.0.0.1
REDIS_PORT=6385
REDIS_PASSWORD=your-redis-password-here
REDIS_CONNECT_TIMEOUT_MS=5000
QUEUE_ENABLED=true
# CACHE_ENABLED=true

# =============================================================================
# MEDIA (disable to prevent memory floods on reconnect)
# =============================================================================
MEDIA_DOWNLOAD_ENABLED=false
STORE_EPHEMERAL_MESSAGES=false

# =============================================================================
# v0.10.9+ FEATURES
# =============================================================================
# Typing indicator: auto-simulates typing before sending (anti-ban).
# Bridge also sends its own typing indicator before non-template/non-reaction
# messages. Disable one or the other to avoid redundancy.
SIMULATE_TYPING=true

# LID (Linked Identity) phone resolution: resolves @lid JIDs to phone numbers.
RESOLVE_LID_TO_PHONE=true

# Channel support: enables WhatsApp Channels API endpoints.
CHANNELS_ENABLED=true

# Status (Stories) support: enables status/story webhook events.
STATUS_ENABLED=true

# =============================================================================
# v0.12.x FEATURES (OpenWA 0.12.0+)
# =============================================================================
# Pin the WhatsApp Web locale so the onboarding-modal auto-dismissal detector
# matches. PUPPETEER_ARGS REPLACES the default flag list — repeat existing flags.
# PUPPETEER_ARGS=--no-sandbox,--disable-setuid-sandbox,--disable-dev-shm-usage,--disable-gpu,--lang=en-US
#
# Accept additional "Continue" button labels for a localised onboarding modal
# (comma-separated). Only needed if your WhatsApp Web renders non-English.
# WWEBJS_ONBOARDING_CONTINUE_LABELS=Continuer,Continuar
```

> **Key settings explained:**
> - `CORS_ORIGINS` — set to your Frappe server URL so the OpenWA dashboard loads in-browser
> - `CSP_UPGRADE_INSECURE_REQUESTS=false` — required when accessing dashboard over plain HTTP (no TLS proxy)
> - `SSRF_ALLOWED_HOSTS` — must include your Frappe **site name**, **server IP**, and any internal services like MinIO (e.g., `SSRF_ALLOWED_HOSTS=your-site-name,your-server-ip,localhost,127.0.0.1,minio`). This keeps global SSRF protection ON while allowing local Frappe ↔ OpenWA communication
>   - `your-site-name` = your Frappe/ERPNext site name (e.g., `my-site`)
>   - `your-server-ip` = the IP used to connect from VM to host (e.g., `192.168.1.15`)
>   - `minio` = MinIO service hostname (if used for file storage)
> - `REDIS_PORT=6385` — use 6385 when ERPNext shares the server (see [Redis Isolation](#redis-isolation-erpnext--openwa-on-same-server)); use 6379 if OpenWA is alone
> - `AUTO_START_SESSIONS=true` — auto-reconnects WhatsApp on OpenWA restart
> - `SIMULATE_TYPING=true` — OpenWA built-in typing simulation (anti-ban). Bridge also has its own auto-typing before sends — see [Typing Indicator Redundancy](#typing-indicator-redundancy) below

**Important**: If `~/OpenWA/data/.env.generated` exists, delete it -- it overrides your `.env`:

```bash
rm ~/OpenWA/data/.env.generated
```

### 3. Start OpenWA with systemd (Production)

systemd keeps OpenWA running across server reboots and auto-restarts on crash:

```bash
sudo tee /etc/systemd/system/openwa.service << 'EOF'
[Unit]
Description=OpenWA WhatsApp API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/Manaa-soft/OpenWA
ExecStart=/usr/bin/node dist/main
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openwa
sudo systemctl restart openwa
sudo systemctl status openwa
```

**Important**: If Puppeteer can't find Chrome, the service may need to run as the user who installed Puppeteer (not root):

```bash
# Change User=root to User=your-username in the service file
sudo sed -i 's/User=root/User=Manaa-soft/' /etc/systemd/system/openwa.service
sudo systemctl daemon-reload
sudo systemctl restart openwa
```

```bash
# Verify it works
curl http://localhost:2785/api/sessions

# Test reboot persistence
sudo reboot
# Wait 30 seconds, SSH back in:
sudo systemctl status openwa  # should show "active (running)"
```

**systemd boot chain**: `[VM Boots] → [systemd launches OpenWA directly] → [OpenWA auto-starts WhatsApp sessions]`

### 4. Connect WhatsApp

Scan the QR code with your phone:
- OpenWA dashboard: http://localhost:2886
- Or start manually: `cd ~/OpenWA && npm run start`

### 5. Get Session Info

```bash
curl http://localhost:2785/api/sessions
# Copy the sessionId (UUID) from the response
```

Get the API key from the OpenWA dashboard (localhost:2886).

### 6. Create Webhook in OpenWA

Via dashboard or API:
- URL: `https://your-site.local/api/method/openwa_bridge.inbound.receive_openwa_message`
- Events: message.received, message.sent, message.ack, message.failed, message.revoked, message.reaction, message.edited, session.status, session.qr, session.authenticated, session.disconnected, session.reconnect_loop, group.join, group.leave, group.update, call.received, status.received

### 7. Configure Frappe

1. Go to **WhatsApp > WhatsApp Account**
2. Enable OpenWA section:
   - OpenWA Enabled: checked
   - OpenWA Base URL: `http://localhost:2785`
   - OpenWA Session ID: `<your-session-uuid>`
   - OpenWA API Key: `<your-api-key>`
   - OpenWA Webhook Secret: `<your-shared-secret>`

### 8. Install Dependencies

```bash
bench pip install PyMuPDF  # For dynamic image headers
```

---

## Redis Isolation (ERPNext + OpenWA on Same Server)

**DO NOT modify `/etc/redis/redis.conf`** if ERPNext is running on the same server. ERPNext already manages its own Redis instances on ports 6379 (Queue), 6380 (Cache), and 6381 (Socketio).

### Why the global config breaks ERPNext

| Problem | Cause |
|---|---|
| Background jobs stall/die | `requirepass` in global config blocks Frappe workers (they don't have the password) |
| Jobs disappear randomly | `allkeys-lru` eviction kills active job keys when memory fills up |
| Real-time updates break | Socketio loses its Redis connection |

### Solution: Dedicated Redis instance for OpenWA

Create a separate Redis instance on a different port (e.g., 6385):

**Step 1: Create config file**

```bash
sudo nano /etc/redis/redis-openwa.conf
```

```plaintext
# Configuration for OpenWA BullMQ
include /etc/redis/redis.conf
port 6385
pidfile /run/redis/redis-server-openwa.pid
logfile /var/log/redis/redis-server-openwa.log
dbfilename dump-openwa.rdb

# Isolation security and sizing
requirepass your-redis-password-here
maxmemory 256mb
maxmemory-policy allkeys-lru
```

**Step 2: Create systemd service**

```bash
sudo cp /lib/systemd/system/redis-server.service /etc/systemd/system/redis-openwa.service
sudo nano /etc/systemd/system/redis-openwa.service
```

Change the `ExecStart=` line:

```plaintext
ExecStart=/usr/bin/redis-server /etc/redis/redis-openwa.conf
```

**Step 3: Start and verify**

```bash
sudo systemctl daemon-reload
sudo systemctl enable redis-openwa
sudo systemctl start redis-openwa

# Verify it works on port 6385
redis-cli -p 6385 -a your-redis-password-here ping
# PONG
```

**Step 4: Point OpenWA to the dedicated instance**

In OpenWA's `.env` or BullMQ config:

```bash
REDIS_URL=redis://:your-redis-password-here@127.0.0.1:6385
```

### Port allocation summary

| Port | Service | Purpose |
|---|---|---|
| 6379 | Frappe Redis Queue | Background workers (ERPNext) |
| 6380 | Frappe Redis Cache | Doc cache (ERPNext) |
| 6381 | Frappe Redis Socketio | Real-time events (ERPNext) |
| **6385** | **OpenWA Redis** | **BullMQ job queue (OpenWA)** |

---

## Why These Steps Matter

| Problem | Cause | Fix |
|---|---|---|
| Sessions don't auto-start on boot | `AUTO_START_SESSIONS=false` in `.env.generated` | Delete `.env.generated`, set `AUTO_START_SESSIONS=true` in `.env` |
| OpenWA dies after server reboot | No systemd setup | `systemctl enable openwa` |
| Dashboard unreachable from browser (`ERR_CONNECTION_REFUSED`) | `HOST` not set — Node.js binds to `127.0.0.1` only | Add `HOST=0.0.0.0` to `.env` and restart. VS Code SSH tunnels mask this issue. |
| Frappe can't reach OpenWA | SSRF blocks private IPs | `SSRF_ALLOWED_HOSTS=your-site-name,your-server-ip,localhost,127.0.0.1,minio` |
| "Could not find Chrome" | Wrong user or missing Chrome | See Chrome/Puppeteer section below |
| `send-image` returns 500 | WhatsApp Web.js returns `undefined` for media | Apply OpenWA media send patch (see below) |
| `send-template` returns 404 | Template deleted when session recreated | Bridge auto-recovers: looks up by name, re-creates if missing |
| Template not found in error logs | Stale `openwa_template_id` after session recreate | Re-save template in Frappe to re-sync, or let outbox auto-recover |
| Messages stuck as Pending | Session dead or API key wrong | Check `curl http://localhost:2785/api/sessions`, verify session ID matches Frappe |
| Session shows "Action Required" (0.12.0+) | WhatsApp "What's new" onboarding modal not dismissed | Acknowledge the modal once in a browser signed in as that account, then use the **Recover** button (stop→start, no QR rescan) |
| Sends return 409 with a live engine | Session in reconnect backoff (`disconnected` + engine loaded) | Do nothing — the gateway reconnects automatically. `start` answers 400 during backoff |

### systemd Boot Chain

```
[VM Boots] -> [systemd launches OpenWA] -> [OpenWA auto-starts WhatsApp sessions]
```

- `systemctl enable openwa` -- registers OpenWA as a systemd service
- `Restart=always` + `RestartSec=5` -- auto-restarts on crash
- `WorkingDirectory` -- tells OpenWA where to find `.env` and data

### Chrome / Puppeteer Not Found

Puppeteer looks for Chrome in the **running user's** cache directory:
- Root: `/root/.cache/puppeteer/`
- Your user: `/home/your-username/.cache/puppeteer/`

**Fix** (pick one):
1. Change systemd `User=root` to `User=your-username`
2. Set `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium` in `.env`
3. Install Chrome for root: `sudo npx puppeteer browsers install chrome`

### OpenWA Media Send Patch (500 on send-image)

WhatsApp Web.js sometimes returns `undefined` for media sends when the session is partially degraded. This causes `TypeError: Cannot read properties of undefined (reading 'id')` in OpenWA.

**Symptoms**: text/template messages work, but `send-image` returns 500.

**Fix**: Patch the compiled OpenWA adapter:

```bash
cd /home/Manaa-soft/OpenWA

# Backup
cp dist/engine/adapters/whatsapp-web-js.adapter.js dist/engine/adapters/whatsapp-web-js.adapter.js.bak

# Patch: add null check in sendMediaMessage
python3 -c "
with open('dist/engine/adapters/whatsapp-web-js.adapter.js', 'r') as f:
    c = f.read()

idx = c.find('sendMediaMessage')
ret_idx = c.find('return { id: msg.id._serialized', idx)
if ret_idx > 0:
    c = c[:ret_idx] + 'if (!msg) { throw new Error(\"Media send returned undefined - session may need reconnect\"); } ' + c[ret_idx:]
    with open('dist/engine/adapters/whatsapp-web-js.adapter.js', 'w') as f:
        f.write(c)
    print('PATCHED')
else:
    print('Pattern not found — check file manually')
"

# Restart
sudo systemctl restart openwa
```

### Template Troubleshooting

**Template 404 errors** (`Template with id '...' not found`):
- Happens when session is deleted+recreated — templates are session-scoped
- Bridge auto-recovers: looks up by name on OpenWA, re-creates if missing
- If recovery fails, re-save the template in Frappe to force re-sync

**Template variables mismatch**:
- Frappe uses numbered placeholders: `{{1}}`, `{{2}}`
- Bridge converts to OpenWA format: `{{param1}}`, `{{param2}}`
- If you create templates directly in OpenWA UI with named placeholders (e.g. `{{customer}}`), variables won't match — create in Frappe instead

**Template not sending**:
- Check `openwa_template_id` is populated on the WhatsApp Templates doc
- Check `openwa_synced` is checked
- Re-save the template to trigger re-sync
- Check Error Logs for "OpenWA Template Sync Failed"

---

## Typing Indicator Redundancy

OpenWA v0.10.9+ has a built-in `SIMULATE_TYPING=true` env var that automatically simulates typing before every message send (anti-ban). The bridge also sends its own typing indicator via `POST /chats/typing` before each non-template, non-reaction message.

**This means typing indicators may fire twice.** Choose one approach:

- **Keep OpenWA's `SIMULATE_TYPING=true`** (recommended): Simpler, built-in anti-ban logic. Disable bridge's auto-typing by setting `openwa_auto_typing=0` in OpenWA Bridge Settings.
- **Disable OpenWA's `SIMULATE_TYPING=false`**: Use bridge's auto-typing only. Set `SIMULATE_TYPING=false` in OpenWA's `.env`. The bridge sends a "typing" indicator for 3 seconds, then a "paused" indicator, then sends the message.

Both approaches produce the same result — the recipient sees "typing..." before the message arrives.

---

## OpenWA 0.12.x Behaviour (bridge support)

The bridge is aware of the OpenWA 0.12.x session-lifecycle changes:

| Feature | What changed | Bridge behaviour |
|---|---|---|
| `engineLoaded` (0.12.1) | Session payload reports whether the gateway holds a live engine | Health check only restarts `disconnected` sessions **without** a live engine. A reconnecting session (engine loaded) is left alone — `start` would answer 400 there. |
| `action_required` status (0.12.0) | New status when the "What's new" onboarding modal needs a human; sends return 409 until cleared | Health check auto-runs **stop→start** once (no QR rescan). If still stuck, the account shows "Action Required" with `lastError` and a **Recover** button. Acknowledging the modal once in a browser signed in as that account clears it permanently. |
| `POST /sessions/:id/logout` (0.12.0) | New endpoint that unlinks the device from the WhatsApp account | New **Unlink** button on the WhatsApp Account page. `200` → device removed from Linked Devices, credentials wiped (fresh QR scan required). `502` `SESSION_LOGOUT_INCOMPLETE` → session stopped locally, retry after starting. |
| `force-kill` returns `400` (0.12.0) | Breaking: force-kill with no live engine now answers 400 instead of 200 | Health check no longer force-kills `failed` sessions (evicted by design) — it deletes and recreates them directly. |
| `409 SESSION_NAME_TEARDOWN_PENDING` (0.12.0) | Retryable 409 on `start`/`delete` while a prior logout owns cleanup | Bridge retries briefly before treating it as a failure. |

**Note:** upgrade OpenWA to **0.12.1+** so `engineLoaded` is present. On 0.12.0 (field absent) the bridge falls back to the pre-0.12 behaviour for `disconnected` sessions.

**0.12.2–0.12.4 (internal releases):** no API-contract changes — `openapi.json` is byte-identical to 0.12.1 — so the bridge needs no changes for them. Recommended version is **0.12.4** (includes the 0.12.3 `AUTO_START_SESSIONS` fix). Deployment note: since 0.12.3, `.env.example` no longer pins a full settings file — do **not** blindly `cp .env.example .env`; copy only the variables you actually override (e.g. `AUTO_START_SESSIONS=true`).

---

## Testing Checklist

### Basic Message Flow
- [ ] Send plain text from Desk -> verify received on phone
- [ ] Send image from Desk -> verify received on phone
- [ ] Reply to a message -> verify quoted context shows
- [ ] React to a message -> verify emoji shows

### Template Sync
- [ ] Create WhatsApp Template -> verify `Synced to OpenWA` checked
- [ ] Verify `OpenWA Template ID` populated
- [ ] Update template -> verify sync succeeds
- [ ] Delete template from OpenWA dashboard -> re-save in Frappe -> verify recovery

### Notification Flow
- [ ] Configure WhatsApp Notification with `openwa_send_type=Template`
- [ ] Set Fields child table with correct field names
- [ ] Submit Sales Invoice via POS -> verify template received on phone
- [ ] Check Error Logs for any failures

### Outbox & Reliability
- [ ] Verify OpenWA Outbox entries are created on message send
- [ ] Verify outbox status transitions: Pending → Sending → Sent
- [ ] Check that retry works: temporarily break OpenWA, verify exponential backoff
- [ ] Verify circuit breaker: 5 consecutive failures → 5 min cooldown
- [ ] Verify scheduler safety-net picks up orphaned Pending entries

### Inbound Security
- [ ] Verify HMAC signature verification (valid signature → pass)
- [ ] Verify lenient mode (no signature + secret configured → warn, not reject)
- [ ] Verify rate limiting (60+ requests from same IP → 429 response)
- [ ] Verify idempotency (duplicate message → rejected)

### Dynamic Image Header
- [ ] Check `Dynamic Header` on template
- [ ] Set Print Format (e.g., "Sales Invoice Standard")
- [ ] Verify PyMuPDF installed: `bench pip install PyMuPDF`
- [ ] Submit Sales Invoice -> verify image received, then template text

### Inbound Messages
- [ ] Send message from phone -> verify WhatsApp Message doc created
- [ ] Verify Communication record created
- [ ] Verify new contact/lead created for unknown numbers
- [ ] Send reaction -> verify reaction recorded

### OpenWA 0.12.x
- [ ] Reconnecting session (`disconnected` + engine loaded) is NOT restarted by the health check
- [ ] `action_required` session auto-recovers via stop→start; if stuck, account shows "Action Required" + Recover button
- [ ] Unlink button removes the device from the account's Linked Devices (fresh QR required to reconnect)
- [ ] Unlink while not started returns a clear "not started" error

---

## Monitoring

### Check Error Logs
```
Frappe Desk -> Setup -> Error Log
Filter by: "OpenWA"
```

### Check OpenWA Logs
```bash
# OpenWA stdout/stderr
journalctl -u openwa -f

# Or check OpenWA dashboard at localhost:2886
```

### Test OpenWA API Directly
```bash
# Health check
curl http://localhost:2785/api/sessions

# Send test text
curl -X POST http://localhost:2785/api/sessions/<sessionId>/messages/send-text \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <api-key>" \
  -d '{"chatId": "967777715787@c.us", "text": "Test from API"}'
```

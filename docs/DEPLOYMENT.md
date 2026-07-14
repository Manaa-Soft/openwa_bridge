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
git pull --no-rebase origin feature/enterprise-queue-retry

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
git clone https://github.com/Open-WA/whatsapp-web.js.git OpenWA
cd OpenWA
npm install
npm run build
```

### 2. Configure OpenWA Environment

OpenWA uses `.env` files for configuration. The key settings:

```bash
# ~/OpenWA/.env (create or edit)
AUTO_START_SESSIONS=true
PORT=2785
SSRF_ALLOWED_HOSTS=192.168.1.15,localhost
```

**Important**: If `~/OpenWA/data/.env.generated` exists, delete it -- it overrides your `.env` and may force `AUTO_START_SESSIONS=false`:

```bash
rm ~/OpenWA/data/.env.generated
```

### 3. Start OpenWA with PM2 (Production)

PM2 keeps OpenWA running across server reboots:

```bash
# Install PM2 globally
npm install -g pm2

# Start OpenWA in production mode
cd ~/OpenWA
pm2 start dist/main.js --name "openwa-gateway"

# Set up PM2 to auto-start on boot
pm2 startup systemd
# Copy and run the sudo command it prints (see note below)

# Save the current process list
pm2 save
```

**The `pm2 startup systemd` step**: PM2 will print a `sudo env PATH=...` command. Copy and run that exact command. It hooks PM2 into Ubuntu's boot system so OpenWA restarts automatically after reboots.

```bash
# Verify it works
pm2 list

# Test reboot persistence
sudo reboot
# Wait 30 seconds, SSH back in, run:
pm2 list  # should show "openwa-gateway" as "online"
```

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
- Events: message.received, message.ack, message.failed, session.status

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

## Why These Steps Matter

| Problem | Cause | Fix |
|---|---|---|
| Sessions don't auto-start on boot | `AUTO_START_SESSIONS=false` in `.env.generated` | Delete `.env.generated`, set `AUTO_START_SESSIONS=true` in `.env` |
| OpenWA dies after server reboot | No PM2/systemd setup | `pm2 startup` + `pm2 save` |
| Frappe can't reach OpenWA | SSRF blocks private IPs | `SSRF_ALLOWED_HOSTS=192.168.1.15,localhost` |

### PM2 Boot Chain

```
[VM Boots] -> [systemd launches PM2] -> [PM2 launches OpenWA] -> [OpenWA auto-starts WhatsApp sessions]
```

- `pm2 startup systemd` -- registers PM2 as a systemd service
- The generated `sudo` command -- creates the permanent boot hook
- `pm2 start dist/main.js` -- runs production code (not dev server)
- `pm2 save` -- snapshots running processes for boot recovery

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

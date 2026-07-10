# Deployment Guide

## Server Details

| | |
|---|---|
| **Server IP** | 192.168.1.15 |
| **OS** | Linux (Ubuntu/Debian) |
| **Frappe bench** | ~/frappe-bench |
| **Site** | your-site.local |
| **OpenWA** | /path/to/OpenWA-main (npm-based) |

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
# On local machine
cd openwa_bridge-develop
git add -A
git commit -m "description of changes"
git push origin develop

# On server
cd ~/frappe-bench
bench get-app https://github.com/Manaa-Soft/openwa_bridge.git --branch develop
bench --site your-site.local migrate
bench restart
```

**Note**: GitHub PAT may be required for push (password auth disabled).

---

## Initial Setup

### 1. Install OpenWA

```bash
# On server
npm install -g @open-wa/wa-automate

# Or if using the source:
cd /path/to/OpenWA-main
npm install
npm run build
```

### 2. Configure OpenWA

Create/edit `.env` in OpenWA directory:

```bash
PORT=2785
SSRF_ALLOWED_HOSTS=192.168.1.15,localhost
```

### 3. Start OpenWA

```bash
cd /path/to/OpenWA-main
npm run start
# Or: node dist/main.js
```

Scan QR code with WhatsApp to connect.

### 4. Get Session Info

After connecting, get the session ID:
```bash
curl http://localhost:2785/api/sessions
# Find your session in the response, copy the sessionId (UUID)
```

Get the API key from OpenWA dashboard (localhost:2886) or config.

### 5. Create Webhook in OpenWA

Via dashboard or API:
- URL: `https://your-site.local/api/method/openwa_bridge.inbound.receive_openwa_message`
- Events: message, message.any, message.reaction

### 6. Configure Frappe

1. Go to **WhatsApp > WhatsApp Account**
2. Enable OpenWA section:
   - OpenWA Enabled: ✓
   - OpenWA Base URL: `http://localhost:2785`
   - OpenWA Session ID: `<your-session-uuid>`
   - OpenWA API Key: `<your-api-key>`

### 7. Install Dependencies

```bash
bench pip install PyMuPDF  # For dynamic image headers
```

---

## Testing Checklist

### Basic Message Flow
- [ ] Send plain text from Desk → verify received on phone
- [ ] Send image from Desk → verify received on phone
- [ ] Reply to a message → verify quoted context shows
- [ ] React to a message → verify emoji shows

### Template Sync
- [ ] Create WhatsApp Template → verify `Synced to OpenWA` checked
- [ ] Verify `OpenWA Template ID` populated
- [ ] Update template → verify sync succeeds
- [ ] Delete template from OpenWA dashboard → re-save in Frappe → verify recovery

### Notification Flow
- [ ] Configure WhatsApp Notification with `openwa_send_type=Template`
- [ ] Set Fields child table with correct field names
- [ ] Submit Sales Invoice via POS → verify template received on phone
- [ ] Check Error Logs for any failures

### Dynamic Image Header
- [ ] Check `Dynamic Header` on template
- [ ] Set Print Format (e.g., "Sales Invoice Standard")
- [ ] Verify PyMuPDF installed: `bench pip install PyMuPDF`
- [ ] Submit Sales Invoice → verify image received, then template text

### Inbound Messages
- [ ] Send message from phone → verify WhatsApp Message doc created
- [ ] Verify Communication record created
- [ ] Verify new contact/lead created for unknown numbers
- [ ] Send reaction → verify reaction recorded

---

## Monitoring

### Check Error Logs
```
Frappe Desk → Setup → Error Log
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

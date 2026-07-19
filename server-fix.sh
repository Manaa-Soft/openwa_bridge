#!/bin/bash
# OpenWA Server Fixes — Run on server as root or with sudo
# Generated from log analysis on 2026-07-19

set -e

OPENWA_DIR="$HOME/OpenWA"
FRAPPE_DIR="$HOME/frappe-bench"

echo "=== 1. Deploy latest code ==="
cd "$FRAPPE_DIR"
bench update --site manaa-soft
bench migrate
bench restart

echo ""
echo "=== 2. Fix Redis port (6379 → 6385) ==="
cd "$OPENWA_DIR"
sed -i 's/REDIS_PORT=6379/REDIS_PORT=6385/' .env
grep -q 'REDIS_PORT=6385' .env || echo 'REDIS_PORT=6385' >> .env

echo ""
echo "=== 3. Set CORS_ORIGINS for dashboard access ==="
if ! grep -q 'CORS_ORIGINS=' .env; then
    echo 'CORS_ORIGINS=http://192.168.1.15' >> .env
    echo "Added CORS_ORIGINS"
else
    echo "CORS_ORIGINS already set:"
    grep 'CORS_ORIGINS=' .env
fi

echo ""
echo "=== 4. Disable CSP upgrade-insecure-requests ==="
if ! grep -q 'CSP_UPGRADE_INSECURE_REQUESTS=' .env; then
    echo 'CSP_UPGRADE_INSECURE_REQUESTS=false' >> .env
    echo "Added CSP_UPGRADE_INSECURE_REQUESTS=false"
else
    sed -i 's/CSP_UPGRADE_INSECURE_REQUESTS=.*/CSP_UPGRADE_INSECURE_REQUESTS=false/' .env
    echo "Updated CSP_UPGRADE_INSECURE_REQUESTS=false"
fi

echo ""
echo "=== 5. Enable auto-start sessions ==="
if ! grep -q 'AUTO_START_SESSIONS=' .env; then
    echo 'AUTO_START_SESSIONS=true' >> .env
    echo "Added AUTO_START_SESSIONS=true"
else
    sed -i 's/AUTO_START_SESSIONS=.*/AUTO_START_SESSIONS=true/' .env
    echo "Updated AUTO_START_SESSIONS=true"
fi

echo ""
echo "=== 6. Fix stale max_attempts ==="
cd "$FRAPPE_DIR"
bench --site manaa-soft execute frappe.client.set_value --args '["WhatsApp Account", "manaa", "openwa_max_outbox_attempts", 100]'
echo "Set openwa_max_outbox_attempts = 100"

echo ""
echo "=== 7. Restart OpenWA with new .env ==="
sudo systemctl restart openwa

echo ""
echo "=== 8. Verify ==="
echo "OpenWA status:"
sudo systemctl status openwa --no-pager -l | head -20
echo ""
echo "Redis port in .env:"
grep 'REDIS_PORT=' "$OPENWA_DIR/.env"
echo ""
echo "Done. Verify dashboard at http://localhost:2785"
echo "Verify OpenWA webhooks by sending a test message from ERPNext."

#!/usr/bin/env bash
# Deploy (or update) the WiFind hub on the Vultr server.
#   server/deploy/deploy_hub.sh                 # uses HUB_IP below
# Needs: SSH access as root (ssh-add your key first), WIFIND_EDGE_TOKEN in server/.env.
# Caddy serves https://<ip-with-dashes>.sslip.io with a free Let's Encrypt
# certificate (Roblox only calls HTTPS) and passes everything to the hub on :9000.
set -euo pipefail
HUB_IP="${HUB_IP:-64.177.42.5}"
HOST="${HUB_IP//./-}.sslip.io"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$HERE/.."
TOKEN="$(grep '^WIFIND_EDGE_TOKEN=' "$SERVER/.env" | cut -d= -f2-)"
[ -n "$TOKEN" ] || { echo "WIFIND_EDGE_TOKEN missing in server/.env"; exit 1; }

ssh root@"$HUB_IP" 'mkdir -p /opt/wifind/app/static'
scp -q "$SERVER/hub.py" root@"$HUB_IP":/opt/wifind/app/hub.py
scp -q "$SERVER/static/"* root@"$HUB_IP":/opt/wifind/app/static/
scp -q "$HERE/wifind-hub.service" root@"$HUB_IP":/etc/systemd/system/wifind-hub.service
ssh root@"$HUB_IP" "set -e
umask 077; printf 'WIFIND_EDGE_TOKEN=%s\n' '$TOKEN' > /etc/wifind.env
chown -R wifind:wifind /opt/wifind/app
printf '%s {\n\tencode gzip\n\treverse_proxy 127.0.0.1:9000\n}\n' '$HOST' > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now wifind-hub >/dev/null 2>&1
systemctl restart wifind-hub
systemctl reload caddy || systemctl restart caddy"
echo "Deployed: https://$HOST  (health: https://$HOST/healthz)"

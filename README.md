# zhilian-mcp (智联招聘 MCP)
Tools: login_qr, login_status, search_internships(keywords, city, page), get_job(job), apply_job(job, confirm=False)

## Aliyun deploy (XHS pattern)
```bash
mkdir -p /opt/zhilian/{src,data} && rsync -a zhilian-mcp/ /opt/zhilian/src/   # copy from box/Mac
openssl rand -hex 32 > /opt/zhilian/token && chmod 600 /opt/zhilian/token
cd /opt/zhilian/src && docker compose up -d --build
echo "ZHILIAN_TOKEN=$(cat /opt/zhilian/token)" > /etc/caddy/zhilian.env && chmod 600 /etc/caddy/zhilian.env
# add EnvironmentFile=/etc/caddy/zhilian.env to caddy.service drop-in (systemctl edit caddy), append deploy/Caddyfile.snippet
systemctl daemon-reload && systemctl restart caddy
cp deploy/cloudflared-zhilian.service /etc/systemd/system/ && systemctl enable --now cloudflared-zhilian
journalctl -u cloudflared-zhilian | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
```
Connector: `https://<tunnel>/mcp` + header `Authorization: Bearer <token>`.

## Login
Call `login_qr` → returns base64 PNG (also /opt/zhilian/data/qr.png) → scan with 智联 App/WeChat → `login_status` until logged_in → saved to /data/storage.json (survives restarts).

## Local test
See [DEV.md](DEV.md) for compose, `/mcp` initialize, the WeChat QR login flow, and how ECS deploy differs (`Dockerfile.ecs`).

`docker compose up --build` then `curl -s -X POST localhost:18064/mcp -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'`

## Docker images
- `Dockerfile` — full Playwright base (local / CI).
- `Dockerfile.ecs` — `FROM shixiseng-mcp:latest` for Aliyun 2GB (avoids OOM). On ECS prefer this; do not overwrite with the heavy image.

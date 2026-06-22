# QuoteHub — Deploy with Nginx Proxy Manager (LAN + SSL + WebUI)

## Objective

- Application accessible via: https://quodb.electrasia.com
- Only available inside internal network (LAN)
- SSL enabled
- Managed via WebUI (Nginx Proxy Manager)

---

## Architecture

Browser (LAN)
   ↓ HTTPS
Nginx Proxy Manager (WebUI + Proxy)
   ↓ HTTP
QuoteHub (container :8000)

---

## Step 1 — Install Nginx Proxy Manager (with WebUI)

Create docker-compose.yml:

```yaml
version: "3"

services:
  npm:
    image: jc21/nginx-proxy-manager:latest
    container_name: npm
    restart: unless-stopped
    ports:
      - "80:80"
      - "81:81"   # WebUI
      - "443:443"
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
```

Start it:

```bash
docker compose up -d
```

---

## Step 2 — Access WebUI

Open in browser:

http://<SERVER-IP>:81

Default login:
- Email: admin@example.com
- Password: changeme

---

## Step 3 — Internal DNS

Configure your router or internal DNS:

quodb.electrasia.com → <SERVER-IP>

IMPORTANT:
- Do NOT point to public IP
- Only internal resolution

---

## Step 4 — SSL Certificate

Recommended: DNS Challenge (e.g., Cloudflare)

Steps:
1. Go to SSL Certificates
2. Add Certificate
3. Choose DNS Challenge
4. Provider: Cloudflare
5. Insert API Token

Domain:
*.electrasia.com

---

## Step 5 — Deploy Application

```bash
cd /path/to/quodb
git checkout main
git pull
./deploy.sh
```

Test:

```bash
curl http://localhost:8000/health
```

---

## Step 6 — Create Proxy Host in NPM

In WebUI:

- Domain:
  quodb.electrasia.com

- Forward Host:
  quodb
  or server/container IP

- Port:
  8000

- Scheme:
  http

Enable:
- Block Common Exploits
- Disable WebSockets (if not needed)

---

## Step 7 — Enable SSL

- Select certificate
- Enable:
  - Force SSL
  - HTTP/2

---

## Step 8 — Restrict External Access

Option A — Firewall:

```bash
ufw allow from 192.168.0.0/16 to any port 443
ufw deny 443
```

Option B — NPM Access List:

Allow:
192.168.0.0/16

Apply to Proxy Host

---

## Step 9 — App Configuration

Edit config.json:

```json
{
  "trust_proxy_headers": true
}
```

Restart:

```bash
docker compose restart
```

---

## Step 10 — Testing

Open:

https://quodb.electrasia.com

Verify:
- SSL valid
- Secure cookies
- Login works

---

## Rollback

Disable proxy in NPM

Revert version:

```bash
git checkout <previous-tag>
./deploy.sh
```

---

## Notes

- Domain works only internally
- DNS-based SSL works without public exposure
- WebUI available at:
  http://<SERVER-IP>:81

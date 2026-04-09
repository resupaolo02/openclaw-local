# Deployment Guide — OpenClaw Local

Step-by-step guide to deploying the OpenClaw Local stack on Linux, macOS, or Windows.

---

## Prerequisites (All Platforms)

| Requirement | Details |
|---|---|
| **Docker Engine 24+** | With Docker Compose v2 (`docker compose` — no hyphen) |
| **Domain or DuckDNS subdomain** | Free at [duckdns.org](https://www.duckdns.org/) — needed for HTTPS via Traefik |
| **Google AI Studio API key** | Free — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **OpenRouter API key** | Free, no credit card — [openrouter.ai/keys](https://openrouter.ai/keys) |
| **Telegram Bot Token** *(optional)* | Via [@BotFather](https://t.me/BotFather) on Telegram |

> **GPU not required.** The stack uses external LLM providers (Gemini, OpenRouter) by default. A local LLM (llama-server + NVIDIA GPU) is optional — see [MAINTENANCE.md → Re-enabling Local LLM](MAINTENANCE.md#re-enabling-local-llm-optional).

---

## Architecture Overview

```
traefik (:443 HTTPS)  ─→  path-based routing to hub
hub (:8000)           ─→  single FastAPI monolith (all services consolidated)
openclaw (:18789)     ─→  AI agent (ghcr.io/openclaw/openclaw:latest)
datasette (:8001)     ─→  SQLite DB web browser (optional)
```

The **hub** container replaces 8 former microservices (core-api, chat, monitor, heartbeat, calendar, landing, finance, nutrition) with a single FastAPI process. All modules are in `services/hub/routers/`.

---

## Platform-Specific Setup

### 🐧 Linux (Ubuntu/Debian)

#### 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect
newgrp docker
```

Verify:

```bash
docker --version        # Docker Engine 24+
docker compose version  # Docker Compose v2+
```

#### 2. Clone the Repository

```bash
git clone https://github.com/resupaolo02/openclaw-local.git
cd openclaw-local
```

#### 3. Create Required Directories

```bash
mkdir -p openclaw-data/workspace models traefik/acme
```

#### 4. Configure Environment

```bash
cp .env.example .env
cp openclaw-data/openclaw.json.example openclaw-data/openclaw.json
```

Edit `.env` and fill in your API keys (see [API Key Setup](#api-key-setup) below):

```bash
nano .env
```

Edit `openclaw-data/openclaw.json` — replace all `YOUR_*_HERE` placeholders:

```bash
nano openclaw-data/openclaw.json
```

#### 5. Generate Traefik HTTP Basic Auth

```bash
sudo apt install -y apache2-utils
htpasswd -c traefik/.htpasswd admin
# Enter a strong password when prompted
```

#### 6. Set Up DuckDNS

Create a cron job to keep your IP updated:

```bash
# Replace YOUR_SUBDOMAIN and YOUR_TOKEN
crontab -e
```

Add this line:

```
*/5 * * * * curl -s "https://www.duckdns.org/update?domains=YOUR_SUBDOMAIN&token=YOUR_TOKEN&ip=" > /dev/null 2>&1
```

#### 7. Open Firewall Ports

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 18789/tcp   # openclaw gateway (optional, for LAN access)
```

#### 8. Start the Stack

```bash
docker compose up -d
```

#### 9. Verify Health

```bash
# Check all containers are running
docker compose ps

# Test hub (all services)
docker exec hub curl -sf http://localhost:8000/health && echo "✅ hub"

# Test openclaw agent
curl -sf http://localhost:18789/health && echo "✅ openclaw"

# Test HTTPS (replace with your domain)
curl -sf https://your-domain.duckdns.org/chat -u admin:yourpassword && echo "✅ HTTPS working"
```

---

### 🍎 macOS

#### 1. Install Docker Desktop

Download and install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/).

Verify:

```bash
docker --version
docker compose version
```

> **Note:** macOS has no NVIDIA GPU support, but this is fine — the stack uses external LLM providers by default. The `llama` service (local LLM) is only needed if you want to run a local model on a Linux machine with a GPU.

#### 2. Clone and Configure

```bash
git clone https://github.com/resupaolo02/openclaw-local.git
cd openclaw-local

mkdir -p openclaw-data/workspace models traefik/acme
cp .env.example .env
cp openclaw-data/openclaw.json.example openclaw-data/openclaw.json
```

Edit both files and fill in your API keys (see [API Key Setup](#api-key-setup)).

#### 3. Generate HTTP Basic Auth

Option A — via Homebrew:

```bash
brew install httpd
htpasswd -c traefik/.htpasswd admin
```

Option B — via Docker (no install needed):

```bash
docker run --rm httpd:2-alpine htpasswd -nb admin yourpassword > traefik/.htpasswd
```

#### 4. Port Forwarding for HTTPS

If you want external access (HTTPS via Traefik/DuckDNS):

- Forward ports **80** and **443** on your router to your Mac's local IP
- Set up DuckDNS the same way as Linux (use `crontab -e` or a LaunchAgent)

For local-only use, skip Traefik and access services directly via the hub:

```bash
# Access chat UI directly (requires port mapping in docker-compose.yml)
# Add "ports: ['8000:8000']" to the hub service temporarily
open http://localhost:8000/chat
```

#### 5. Start the Stack

```bash
docker compose up -d
docker compose ps
```

---

### 🪟 Windows (WSL2)

#### 1. Install WSL2

Open PowerShell as Administrator:

```powershell
wsl --install -d Ubuntu
```

Restart your PC when prompted. Set up your Linux username/password.

#### 2. Install Docker Desktop

Download [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/). During setup:

- ✅ Enable **WSL2 backend** (not Hyper-V)
- ✅ Enable integration with your Ubuntu distro

Verify inside WSL2:

```bash
docker --version
docker compose version
```

#### 3. Clone Inside WSL2 Filesystem

> ⚠️ **Critical:** Clone inside the WSL2 filesystem (e.g., `~/`), **NOT** under `/mnt/c/`. Mounting Windows paths causes severe I/O performance issues and permission problems with Docker volumes.

```bash
# Inside WSL2 terminal
cd ~
git clone https://github.com/resupaolo02/openclaw-local.git
cd openclaw-local
```

#### 4. Configure (Same as Linux)

```bash
mkdir -p openclaw-data/workspace models traefik/acme
cp .env.example .env
cp openclaw-data/openclaw.json.example openclaw-data/openclaw.json
```

Edit `.env` and `openclaw-data/openclaw.json` with your API keys.

#### 5. Generate HTTP Basic Auth

```bash
sudo apt update && sudo apt install -y apache2-utils
htpasswd -c traefik/.htpasswd admin
```

#### 6. Windows Firewall

If you want external access, allow ports through Windows Firewall (PowerShell as Admin):

```powershell
New-NetFirewallRule -DisplayName "OpenClaw HTTP"  -Direction Inbound -LocalPort 80  -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "OpenClaw HTTPS" -Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow
```

#### 7. Port Forwarding for External Access

Forward WSL2 ports to the Windows host (PowerShell as Admin):

```powershell
# Get your WSL2 IP
wsl hostname -I

# Forward ports (replace WSL_IP with the output above)
netsh interface portproxy add v4tov4 listenport=80  listenaddress=0.0.0.0 connectport=80  connectaddress=WSL_IP
netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=443 connectaddress=WSL_IP
```

> **Note:** WSL2 IPs change on reboot. Automate this with a startup script or use [wsl-port-forwarder](https://github.com/microsoft/WSL/issues/4150).

#### 8. Start the Stack

```bash
docker compose up -d
docker compose ps
```

---

## API Key Setup

### Google AI Studio (Gemini) — Primary LLM

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key** → select any project (or create one)
4. Copy the key

Add to both files:

```bash
# .env
LLM_API_KEY=AIza...your-key

# openclaw-data/openclaw.json → models.providers.gemini.apiKey
"apiKey": "AIza...your-key"
```

### OpenRouter — Secondary/Coding LLM

1. Go to [openrouter.ai/keys](https://openrouter.ai/keys)
2. Sign in (GitHub or Google)
3. Click **Create Key** → copy it
4. No credit card needed — free models available (e.g., `qwen/qwen3-coder:free`)

Add to both files:

```bash
# .env
LLM_CODING_KEY=sk-or-v1-...your-key

# openclaw-data/openclaw.json → models.providers.openrouter.apiKey
"apiKey": "sk-or-v1-...your-key"
```

### Telegram Bot (Optional)

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` → follow the prompts to name your bot
3. Copy the bot token (format: `123456789:ABCdefGHI...`)

Add to `openclaw-data/openclaw.json`:

```json
"channels": {
  "telegram": {
    "botToken": "123456789:ABCdefGHI...",
    "enabled": true
  }
}
```

### DuckDNS Token

1. Go to [duckdns.org](https://www.duckdns.org/) and sign in
2. Create a subdomain (e.g., `myname-openclaw`)
3. Copy your token from the top of the page

Add to `.env`:

```bash
DUCKDNS_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CORS_ORIGIN=https://myname-openclaw.duckdns.org
```

---

## Configuration Reference

### `.env` — Full Variable List

| Variable | Required | Description |
|---|---|---|
| `LLM_API_BASE` | Yes | Primary LLM endpoint (default: Gemini) |
| `LLM_API_KEY` | Yes | API key for primary LLM |
| `LLM_MODEL` | Yes | Model name (e.g., `gemini-3-flash-preview`) |
| `LLM_CTX_WINDOW` | No | Context window size in tokens (default: `1000000`) |
| `LLM_CODING_URL` | No | Secondary LLM completions endpoint (OpenRouter) |
| `LLM_CODING_KEY` | No | API key for secondary LLM |
| `LLM_CODING_MODEL` | No | Model name for coding tasks (e.g., `qwen/qwen3-coder:free`) |
| `CORS_ORIGIN` | Yes | Your public domain (e.g., `https://your-domain.duckdns.org`) |
| `DUCKDNS_TOKEN` | Yes | Token for automatic TLS cert renewal |
| `USDA_API_KEY` | No | For nutrition service — defaults to `DEMO_KEY` (rate-limited) |

### `openclaw-data/openclaw.json` — Key Sections

| Section | Purpose |
|---|---|
| `agents.defaults.model` | Primary model + fallbacks for the AI agent |
| `models.providers` | LLM provider configs (base URLs, API keys, model specs) |
| `channels.telegram` | Telegram bot integration |
| `skills.entries` | Enable/disable built-in skills |
| `gateway.auth.token` | Auth token for gateway API access |

---

## First-Time Setup Checklist

```
[ ] .env configured with all API keys
[ ] openclaw-data/openclaw.json configured (providers, channels)
[ ] traefik/.htpasswd created with secure password
[ ] DuckDNS subdomain created and cron job set up
[ ] Required directories exist: openclaw-data/workspace, models, traefik/acme
[ ] docker compose up -d
[ ] All services healthy: docker compose ps
[ ] Test chat UI: https://your-domain.duckdns.org/chat
[ ] Test Telegram bot (if configured): send a message to your bot
[ ] Test monitor dashboard: https://your-domain.duckdns.org/monitor
```

---

## Service Ports & Routes

| Path Prefix | Module | Internal URL | Health Check |
|---|---|---|---|
| `/chat` | chat | `hub:8000/chat` | `GET /chat/api/health` |
| `/monitor` | monitor | `hub:8000/monitor` | `GET /monitor/api/health` |
| `/heartbeat` | heartbeat | `hub:8000/heartbeat` | `GET /heartbeat/api/health` |
| `/calendar` | calendar | `hub:8000/calendar` | `GET /calendar/api/health` |
| `/finance` | finance | `hub:8000/finance` | `GET /finance/api/health` |
| `/nutrition` | nutrition | `hub:8000/nutrition` | `GET /nutrition/api/health` |
| `/data` | datasette | `datasette:8001` | `GET /` |
| `/` (catch-all) | landing | `hub:8000` | `GET /health` |
| *(port 18789)* | openclaw | direct | `GET /health` |

---

## Troubleshooting

### Containers Won't Start

```bash
# Check what's failing
docker compose ps
docker compose logs --tail=50 <service-name>

# Common fix: ensure Docker daemon is running
sudo systemctl start docker   # Linux
# On macOS/Windows: start Docker Desktop
```

### Port Conflicts

```bash
# Check what's using a port
sudo lsof -i :443
sudo lsof -i :80

# Kill the conflicting process or change ports in docker-compose.yml
```

### DuckDNS / DNS Not Resolving

```bash
# Test DuckDNS update manually
curl "https://www.duckdns.org/update?domains=YOUR_SUBDOMAIN&token=YOUR_TOKEN&ip="
# Should return: OK

# Check DNS propagation
dig your-domain.duckdns.org
nslookup your-domain.duckdns.org
```

### TLS Certificate Issues

```bash
# Check Traefik logs for cert errors
docker compose logs --tail=100 traefik | grep -i "acme\|cert\|error"

# Reset certs (nuclear option)
rm -rf traefik/acme/acme.json
docker compose restart traefik
```

### Rate Limits (LLM Providers)

- **Gemini free tier:** 15 RPM / 1M TPM — sufficient for personal use
- **OpenRouter free models:** Vary by model — check [openrouter.ai/models](https://openrouter.ai/models)
- **USDA (nutrition service):** `DEMO_KEY` = 30 req/hr — get a free key at [fdc.nal.usda.gov/api-key-signup](https://fdc.nal.usda.gov/api-key-signup) for higher limits

### Service Health Check Failing

```bash
# Quick health check — all modules are in the hub container
docker exec hub curl -sf http://localhost:8000/health && echo "✅ hub (core)"
docker exec hub curl -sf http://localhost:8000/chat/api/health && echo "✅ chat"
docker exec hub curl -sf http://localhost:8000/finance/api/health && echo "✅ finance"
docker exec hub curl -sf http://localhost:8000/nutrition/api/health && echo "✅ nutrition"
docker exec hub curl -sf http://localhost:8000/calendar/api/health && echo "✅ calendar"
docker exec hub curl -sf http://localhost:8000/monitor/api/health && echo "✅ monitor"
docker exec hub curl -sf http://localhost:8000/heartbeat/api/health && echo "✅ heartbeat"
curl -sf http://localhost:18789/health && echo "✅ openclaw"
```

### WSL2-Specific Issues

- **Slow file I/O:** Make sure repo is cloned inside WSL2 filesystem (`~/`), not `/mnt/c/`
- **Port forwarding lost on reboot:** Re-run the `netsh interface portproxy` commands
- **Docker not found in WSL2:** Open Docker Desktop → Settings → Resources → WSL Integration → enable your distro

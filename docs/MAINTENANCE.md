# Maintenance Guide — OpenClaw Local

Day-to-day operations, backups, updates, and configuration for the OpenClaw Local stack.

---

## Service Management

### Start / Stop

```bash
# Start all services
docker compose up -d

# Stop all services (keeps data intact)
docker compose down

# Start/stop a single service
docker compose up -d chat
docker compose stop chat
```

### Rebuild After Code Changes

When you edit a service's source code (e.g., `services/chat/app.py`):

```bash
docker compose build chat && docker compose up -d --no-deps chat
```

The `--no-deps` flag prevents restarting dependent services unnecessarily.

### Rebuild All Services

```bash
docker compose build && docker compose up -d
```

### View Logs

```bash
# Follow logs for a specific service
docker compose logs -f chat

# Last 50 lines from core-api
docker compose logs --tail=50 core-api

# All services at once
docker compose logs -f --tail=20

# Search logs for errors
docker compose logs chat 2>&1 | grep -i error
```

### Restart After Config Changes

```bash
# After editing openclaw-data/openclaw.json or .env
docker compose restart openclaw

# After editing custom-skills/
docker compose restart openclaw

# After editing traefik/dynamic.yml (auto-reloads, but just in case)
docker compose restart traefik
```

### Check Container Status

```bash
# Quick overview
docker compose ps

# Detailed health status
docker inspect --format='{{.Name}} {{.State.Health.Status}}' $(docker compose ps -q) 2>/dev/null
```

---

## Backup & Restore

### What to Back Up

| Path | Contents | Critical? |
|---|---|---|
| `openclaw-data/` | Agent config, sessions, memory, workspace DBs | **Yes** |
| `.env` | API keys, secrets | **Yes** |
| `traefik/.htpasswd` | HTTP basic auth credentials | **Yes** |
| `custom-skills/` | All custom skill definitions | **Yes** |
| `services/` | Microservice source code | Yes (if modified) |
| `traefik/acme/` | TLS certificates (auto-renewed) | Nice to have |
| `models/` | GGUF model files (large, re-downloadable) | No |

### Backup Script

```bash
#!/bin/bash
# backup-openclaw.sh — Run from the project root
BACKUP_DIR="./backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/openclaw-backup-${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

tar czf "$BACKUP_FILE" \
  --exclude='openclaw-data/logs' \
  --exclude='openclaw-data/sandboxes' \
  --exclude='models/' \
  openclaw-data/ \
  custom-skills/ \
  services/ \
  .env \
  traefik/.htpasswd \
  traefik/acme/ \
  docker-compose.yml

echo "✅ Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
```

Make it executable and run:

```bash
chmod +x backup-openclaw.sh
./backup-openclaw.sh
```

### Restore Procedure

```bash
# 1. Stop all services
docker compose down

# 2. Extract backup (from project root)
tar xzf backups/openclaw-backup-YYYYMMDD-HHMMSS.tar.gz

# 3. Rebuild and restart
docker compose build && docker compose up -d

# 4. Verify health
docker compose ps
```

---

## Updating Services

### Update OpenClaw Agent

```bash
# Pull the latest image
docker compose pull openclaw

# Restart with new image
docker compose up -d --no-deps openclaw

# Verify
curl -sf http://localhost:18789/health && echo "✅ openclaw updated"
```

### Update Traefik

```bash
docker compose pull traefik
docker compose up -d --no-deps traefik
```

### Rebuild Local Services After Code Changes

```bash
# Single service
docker compose build chat && docker compose up -d --no-deps chat

# All local services
docker compose build && docker compose up -d
```

### Rolling Updates (Minimal Downtime)

Restart services one at a time to avoid a full outage:

```bash
for svc in core-api monitor heartbeat calendar chat finance nutrition landing datasette; do
  echo "Restarting $svc..."
  docker compose build "$svc" && docker compose up -d --no-deps "$svc"
  sleep 5
done
echo "✅ All services updated"
```

---

## Custom Skills

### Creating a New Skill

1. **Create the skill folder:**

    ```bash
    mkdir custom-skills/my-skill
    ```

2. **Create `SKILL.md`** — must be wrapped in `` ```skill `` fences:

    ````markdown
    ```skill
    ---
    name: my-skill
    description: Use when the user asks about X or Y. Triggers on: "keyword1", "keyword2", "keyword3"
    version: 1.0.0
    metadata: { "openclaw": { "emoji": "🔧" } }
    ---

    # My Skill

    What this skill does.

    ## Workflow

    1. **Step one** — do this
    2. **Step two** — do that

    ## Response Format

    ```
    RESULT: [value]
    ```

    ## Notes

    - Any edge cases
    ```
    ````

3. **Restart OpenClaw** (skills are NOT hot-reloaded):

    ```bash
    docker compose restart openclaw
    ```

4. **Test** by sending a message containing your trigger keywords.

### Key Rules

- `name` must be kebab-case and match the folder name exactly
- `description` must start with "Use when..." and end with `Triggers on: "kw1", "kw2"`
- Large datasets go in `references/` or `assets/` subfolders, not inline
- Full format spec: `custom-skills/SKILL_FORMAT.md`

### Enabling Built-in Skills

Edit `openclaw-data/openclaw.json` under `skills.entries`:

```json
"skills": {
  "entries": {
    "coding-agent": { "enabled": true },
    "weather": { "enabled": true }
  }
}
```

Then restart:

```bash
docker compose restart openclaw
```

### Current Custom Skills

| Skill | Emoji | Purpose |
|---|---|---|
| `self-admin` | 🔧 | System health, restart/rebuild, architecture |
| `ph-credit-card-maximizer` | 💳 | Best card for purchases, rewards |
| `ph-investment-advisor` | 📈 | PH financial planning, digital banks |
| `travel-advisor` | ✈️ | Trip planning, itineraries |
| `media-downloader` | 📥 | Route to epub/audiobook download |
| `epub-downloader` | 📚 | Free EPUBs from Gutenberg/Archive |
| `finance-tracker` | 💰 | Live expense/income tracking |
| `nutrition-tracker` | 🥗 | Calorie & macro tracking |
| `calendar-assistant` | 📅 | Google Calendar integration |

---

## Multi-Model Configuration

### Changing the Default Model

Edit `openclaw-data/openclaw.json`:

```json
"agents": {
  "defaults": {
    "model": {
      "primary": "gemini/gemini-3-flash-preview",
      "fallbacks": [
        "openrouter/qwen/qwen3-coder:free"
      ]
    }
  }
}
```

And update `.env`:

```bash
LLM_MODEL=gemini-3-flash-preview
```

Then restart:

```bash
docker compose restart openclaw chat
```

### Adding a New Provider

Add a new entry under `models.providers` in `openclaw-data/openclaw.json`:

```json
"providers": {
  "gemini": { ... },
  "openrouter": { ... },
  "my-provider": {
    "baseUrl": "https://api.example.com/v1",
    "api": "openai-completions",
    "apiKey": "sk-...",
    "models": [
      {
        "id": "model-name",
        "name": "Display Name",
        "contextWindow": 128000,
        "maxTokens": 8192,
        "input": ["text"],
        "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
        "reasoning": true
      }
    ]
  }
}
```

### Chat Service Model Routing

The chat service (`services/chat/app.py`) supports dual providers:

- **Primary:** Used for general conversation — configured via `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
- **Coding:** Dedicated coding model — configured via `LLM_CODING_URL` / `LLM_CODING_KEY` / `LLM_CODING_MODEL`

These are set in `.env` and passed to the chat container via `docker-compose.yml`:

```bash
# .env
LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
LLM_API_KEY=your-gemini-key
LLM_MODEL=gemini-3-flash-preview

LLM_CODING_URL=https://openrouter.ai/api/v1/chat/completions
LLM_CODING_KEY=your-openrouter-key
LLM_CODING_MODEL=qwen/qwen3-coder:free
```

### Rate Limit Monitoring

- **Gemini free tier:** ~15 RPM / 1M TPM
- **OpenRouter free models:** Varies by model — check [openrouter.ai/models](https://openrouter.ai/models)
- Monitor usage at the provider dashboards:
  - [Google AI Studio](https://aistudio.google.com/) → API key → Usage
  - [OpenRouter](https://openrouter.ai/activity) → Activity

---

## Monitoring & Health

### Monitor Dashboard

Access at `https://your-domain.duckdns.org/monitor` (requires basic auth).

Shows: CPU, RAM, GPU utilization, and all microservice health status.

### Health Check Endpoints

```bash
# OpenClaw agent
curl -sf http://localhost:18789/health

# Core API
curl -sf http://localhost:8000/health

# All microservices (from inside Docker network)
# monitor, heartbeat, calendar, chat, finance, nutrition, landing
curl -sf http://localhost:<port>/api/health

# Datasette
curl -sf http://localhost:8001/
```

### Quick Health Check Script

```bash
#!/bin/bash
echo "=== OpenClaw Health Check ==="
services=("openclaw:18789/health" "core-api:8000/health")
api_services=("monitor:9091" "heartbeat:9092" "calendar:9093" "chat:9094" "landing:9095" "finance:9096" "nutrition:9097")

for svc in "${services[@]}"; do
  name="${svc%%:*}"
  endpoint="${svc}"
  status=$(curl -sf "http://localhost:${endpoint}" -o /dev/null -w "%{http_code}" 2>/dev/null)
  [ "$status" = "200" ] && echo "✅ $name" || echo "❌ $name (HTTP $status)"
done

for svc in "${api_services[@]}"; do
  name="${svc%%:*}"
  port="${svc##*:}"
  status=$(curl -sf "http://localhost:${port}/api/health" -o /dev/null -w "%{http_code}" 2>/dev/null)
  [ "$status" = "200" ] && echo "✅ $name" || echo "❌ $name (HTTP $status)"
done

# Datasette
status=$(curl -sf "http://localhost:8001/" -o /dev/null -w "%{http_code}" 2>/dev/null)
[ "$status" = "200" ] && echo "✅ datasette" || echo "❌ datasette (HTTP $status)"
```

### Docker Container Health

```bash
# View health status of all containers
docker ps --format "table {{.Names}}\t{{.Status}}"

# Check a specific container's health log
docker inspect --format='{{json .State.Health}}' openclaw | python3 -m json.tool
```

---

## Re-enabling Local LLM (Optional)

The project was originally designed with a local **Qwen3.5-9B** model served via llama.cpp. It migrated to external providers (Gemini, OpenRouter) for better quality and no GPU requirement. You can re-enable the local LLM if you have an NVIDIA GPU.

### Requirements

- NVIDIA GPU with ≥10 GB VRAM (e.g., RTX 3060 12GB+)
- [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed

### Step 1: Install NVIDIA Container Toolkit

```bash
# Ubuntu/Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Step 2: Download a GGUF Model

```bash
# Example: Qwen3.5-9B Q4_K_M quantization (~6 GB)
cd models/
wget https://huggingface.co/bartowski/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf
```

### Step 3: Verify llama Service in docker-compose.yml

The `llama` service should already be defined in `docker-compose.yml`. Ensure the model path matches your downloaded file:

```yaml
services:
  llama:
    image: ghcr.io/ggml-org/llama.cpp:server-cuda
    command: >
      --model /models/Qwen3.5-9B-Q4_K_M.gguf
      --host 0.0.0.0
      --port 8080
      --n-gpu-layers 33
      --ctx-size 32768
      ...
```

### Step 4: Add Local Provider to openclaw.json

Add a `local-llama` provider in `openclaw-data/openclaw.json`:

```json
"providers": {
  "local-llama": {
    "baseUrl": "http://llama:8080/v1",
    "api": "openai-completions",
    "apiKey": "not-needed",
    "models": [
      {
        "id": "Qwen3.5-9B-Q4_K_M",
        "name": "Qwen 3.5 9B (Local)",
        "contextWindow": 32768,
        "maxTokens": 4096,
        "input": ["text"],
        "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
        "reasoning": true
      }
    ]
  }
}
```

### Step 5: Update .env for Local LLM

To use the local model as primary for the chat service:

```bash
LLM_API_BASE=http://llama:8080/v1
LLM_API_KEY=not-needed
LLM_MODEL=local
```

Or keep Gemini as primary and use local as a fallback in `openclaw.json`:

```json
"model": {
  "primary": "gemini/gemini-3-flash-preview",
  "fallbacks": [
    "local-llama/Qwen3.5-9B-Q4_K_M",
    "openrouter/qwen/qwen3-coder:free"
  ]
}
```

### Step 6: Start the LLM Service

```bash
docker compose up -d llama
# Wait for model loading (~30-60 seconds)
docker compose logs -f llama
# Look for: "server listening on 0.0.0.0:8080"

# Verify health
docker exec llama curl -sf http://localhost:8080/health && echo "✅ llama-server ready"
```

---

## Security

### Rotate API Keys

1. Generate new keys at the respective provider dashboards
2. Update `.env` and `openclaw-data/openclaw.json`
3. Restart affected services:

    ```bash
    docker compose restart openclaw chat
    ```

### Update HTTP Basic Auth Password

```bash
# Generate new credentials
htpasswd -c traefik/.htpasswd admin
# Enter new password when prompted

# Traefik picks up changes automatically (watches the file)
# Or force reload:
docker compose restart traefik
```

### TLS Certificate Renewal

Certificates are **automatically renewed** by Traefik via Let's Encrypt + DuckDNS DNS challenge. No manual action needed.

To check certificate status:

```bash
docker compose logs traefik | grep -i "acme\|cert\|renew"
```

Certificates are stored in `traefik/acme/acme.json`.

### Security Checklist

```
[ ] .env is in .gitignore (NEVER commit)
[ ] openclaw-data/openclaw.json is in .gitignore (contains API keys)
[ ] traefik/.htpasswd uses a strong password
[ ] DuckDNS token is not exposed in logs
[ ] All public routes require basic auth (configured in traefik/dynamic.yml)
[ ] Security headers enabled (HSTS, X-Frame-Options, etc.)
[ ] Rate limiting active (100 req/min per IP)
```

### Security Headers (Already Configured)

Traefik applies these headers to all routes via `traefik/dynamic.yml`:

- **HSTS:** 1 year, includeSubdomains
- **X-Frame-Options:** DENY
- **X-Content-Type-Options:** nosniff
- **X-XSS-Protection:** enabled
- **Referrer-Policy:** strict-origin-when-cross-origin

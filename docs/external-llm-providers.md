# External LLM Providers for OpenClaw

Your local Qwen3.5-9B model is great for privacy and offline use, but when you need stronger reasoning, faster responses, or a longer context window, you can point OpenClaw at any free external provider.

The stack uses the **OpenAI-compatible API format**, so switching providers is a config-only change — no code changes needed.

---

## Current Setup

| Role | Model | Provider | Intelligence Score |
|---|---|---|---|
| **Default** (general chat, reasoning) | `gemini-3-flash-preview` | Google AI Studio | **46** |
| **Coding** (code gen, debug, refactor) | `qwen/qwen3-coder:free` | OpenRouter | SWE-bench SOTA* |

*\*Qwen3-Coder is comparable to Claude Sonnet 4 on SWE-Bench Verified (82%) — state-of-the-art among open models.*

The chat service **automatically routes** coding tasks to the coding model and everything else to the default.

---

## Why These Models?

Rankings are based on verified data from [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models), [Vellum LLM Leaderboard](https://www.vellum.ai/llm-leaderboard), and direct API testing (April 2026).

### Free Model Intelligence Rankings

| Rank | Model | Provider | AA Score | Speed | Free? |
|---|---|---|---|---|---|
| 1 | **Gemini 3 Flash Preview** | Google AI Studio | **46** | 158 t/s | ✅ 1500 RPD |
| 2 | MiniMax-M2.5 | OpenRouter | 42 | 81 t/s | ✅ :free |
| 3 | Gemma 4 31B | OpenRouter | 39 | 36 t/s | ✅ :free |
| 4 | Step 3.5 Flash | OpenRouter | 38 | 90 t/s | ✅ :free |
| 5 | NVIDIA Nemotron 3 Super | OpenRouter | 36 | 156 t/s | ✅ :free |
| 6 | Gemini 3.1 Flash-Lite | Google AI Studio | 34 | 203 t/s | ✅ |
| 7 | gpt-oss-120B | OpenRouter | 33 | 218 t/s | ✅ :free |

**Gemini 3 Flash Preview is the #1 free model** for general intelligence. Nothing free beats it.

### Why Qwen3-Coder for Coding?

Despite a lower general intelligence score (28), Qwen3-Coder is a **coding specialist**:
- **480B MoE** architecture (35B active per token) — purpose-built for code
- **SWE-Bench Verified**: SOTA among open models, comparable to Claude Sonnet 4
- **Agent RL trained**: 20,000 parallel environments for multi-turn coding tasks
- **7.5T training tokens** (70% code), 262K native context
- Optimized for function calling, tool use, and long-context repo reasoning

Source: [Qwen3-Coder announcement](https://qwenlm.github.io/blog/qwen3-coder/)

---

## How It Works

All three providers expose an OpenAI-compatible `/chat/completions` endpoint. The stack reads these env vars from `.env`:

### Default Provider (all tasks)

| Env Var | Purpose |
|---|---|
| `LLM_API_BASE` | OpenAI-compat base URL — used by `openclaw` agent and `chat` service |
| `LLM_API_KEY` | Bearer token / API key |
| `LLM_MODEL` | Model name to request |
| `LLM_CTX_WINDOW` | Context window size (for history trimming in `chat` service) |

### Coding Provider (auto-routed for coding tasks)

| Env Var | Purpose |
|---|---|
| `LLM_CODING_URL` | Full completions URL for the coding provider (e.g. `https://openrouter.ai/api/v1/chat/completions`) |
| `LLM_CODING_KEY` | API key for the coding provider |
| `LLM_CODING_MODEL` | Coding model name (e.g. `qwen/qwen3-coder:free`) |

The `chat` service derives the default completions URL automatically as `{LLM_API_BASE}/chat/completions`.

---

## Multi-Model Routing

The chat service includes a **keyword-based task classifier** that automatically detects coding tasks and routes them to the coding model.

### How the Classifier Works

The last user message is analyzed for coding signals:

| Signal | Examples |
|---|---|
| **Code blocks** | Any message containing ` ``` ` |
| **Coding actions** | "write code", "create a function", "build a script" |
| **Debugging** | "debug this", "fix the bug", "resolve this error" |
| **Refactoring** | "refactor this code", "optimize the function" |
| **Error patterns** | "syntax error", "runtime error", "traceback" |
| **Code review** | "code review", "review this code", "pull request" |

If no coding signal is detected, the request goes to the **default model**.

### Model Metadata

Each response begins with a metadata SSE event:
```
data: {"type": "model_info", "model": "gemini-3-flash-preview", "category": "default"}
```
or
```
data: {"type": "model_info", "model": "qwen/qwen3-coder:free", "category": "coding"}
```

The UI can use this to display which model answered each message.

### Disabling the Coding Provider

To disable multi-model routing and use only the default model, either:
- Remove `LLM_CODING_URL`, `LLM_CODING_KEY`, and `LLM_CODING_MODEL` from `.env`
- Or set them to empty strings

Then restart:
```bash
docker compose build chat && docker compose up -d --no-deps chat
```

---

## 🥇 Option 1: Google AI Studio — Gemini 3 Flash Preview (Default)

**Best for:** reasoning, coding, math, long documents — **highest-intelligence free model (AA score 46)**

**Verified free tier:**
- 15 requests/minute (RPM)
- 1,500 requests/day (RPD)
- 1,000,000 tokens/minute (input TPM)
- 1M token context window
- No credit card required

### Setup

1. Go to [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey) → **Create API Key** → copy it.

2. Open `.env` and set the **Option 1** block:
   ```env
   LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
   LLM_API_KEY=your-google-ai-studio-key-here
   LLM_MODEL=gemini-3-flash-preview
   LLM_CTX_WINDOW=1048576
   ```

3. Apply the change:
   ```bash
   docker compose up -d --no-deps openclaw chat core-api
   ```

4. Verify:
   ```bash
   docker exec core-api curl -sf http://chat:9094/api/health
   # Then open https://openclaw-frostbite.duckdns.org/chat
   ```

**Other free models (same API key):**
| Model ID | AA Score | Notes |
|---|---|---|
| `gemini-3-flash-preview` | **46** | **Best free model** — reasoning + coding |
| `gemini-3.1-flash-lite-preview` | 34 | Faster (203 t/s), lower quality |
| `gemini-2.5-flash` | ~32 | Older, still solid |

**NOT free:** `gemini-2.5-pro` and `gemini-3.1-pro-preview` — free tier quota is 0.

**Notes:**
- Gemini 3 Flash **supports function calling** — the chat service's `exec` tool works fully.
- Gemini returns `finish_reason: "stop"` (not `"tool_calls"`) for tool calls — our chat service handles both.
- On the free tier, your data is used to improve Google's models (paid tier opts out).

---

## 🥈 Option 2: Groq — Llama 3.3 70B (Fastest)

**Best for:** speed-sensitive tasks, real-time chat

**Verified free tier (Llama 3.3 70B):**
- 30 requests/minute (RPM)
- 1,000 requests/day (RPD)
- 12,000 tokens/minute (TPM)
- 100,000 tokens/day (TPD)
- No credit card required

Groq's LPU (Language Processing Unit) delivers **300–800 tokens/second** — far faster than any cloud GPU provider.

### Setup

1. Go to [https://console.groq.com/keys](https://console.groq.com/keys) → **Create API Key** → copy it.

2. Open `.env` and uncomment the **Option 2** block:
   ```env
   LLM_API_BASE=https://api.groq.com/openai/v1
   LLM_API_KEY=your-groq-api-key-here
   LLM_MODEL=llama-3.3-70b-versatile
   LLM_CTX_WINDOW=131072
   ```

3. Apply the change:
   ```bash
   docker compose up -d --no-deps openclaw chat core-api
   ```

**Other free models on Groq:**
| Model ID | RPD | TPD | Notes |
|---|---|---|---|
| `llama-3.3-70b-versatile` | 1,000 | 100K | Best reasoning |
| `meta-llama/llama-4-scout-17b-16e-instruct` | 1,000 | 500K | Larger token budget |
| `llama-3.1-8b-instant` | 14,400 | 500K | Most requests/day |
| `deepseek-r1-distill-llama-70b` | 1,000 | 100K | Best for math/coding |

**Notes:**
- 100K TPD for Llama 3.3 70B can be tight for heavy usage. Use `llama-3.1-8b-instant` for more volume at lower quality.
- All listed models support function calling.

---

## 🥉 Option 3: OpenRouter — Free Models (Coding Provider)

**Best for:** specialized coding model (Qwen3-Coder), switching between many top models

**Verified free tier:**
- 20 requests/minute (RPM) for `:free` models
- **50 requests/day** without any credit purchase
- **1,000 requests/day** after a one-time $10 top-up (credits never expire, not a subscription)

### Setup as Coding Provider (Recommended)

Use OpenRouter as a **secondary coding provider** alongside Gemini as the default:

1. Go to [https://openrouter.ai/keys](https://openrouter.ai/keys) → **Create Key** → copy it.

2. Open `.env` and set the coding provider section:
   ```env
   # Multi-Model Routing: Coding Provider
   LLM_CODING_URL=https://openrouter.ai/api/v1/chat/completions
   LLM_CODING_KEY=your-openrouter-key-here
   LLM_CODING_MODEL=qwen/qwen3-coder:free
   ```

3. Rebuild and restart chat:
   ```bash
   docker compose build chat && docker compose up -d --no-deps chat
   ```

4. Test coding routing:
   - Ask "Hello, how are you?" → routes to Gemini (default)
   - Ask "Write code for a Python function to sort numbers" → routes to Qwen3-Coder (coding)

### Setup as Primary Provider (Alternative)

Use OpenRouter as the **only** provider (no multi-model routing):

1. Open `.env` and set:
   ```env
   LLM_API_BASE=https://openrouter.ai/api/v1
   LLM_API_KEY=your-openrouter-key-here
   LLM_MODEL=qwen/qwen3-coder:free
   LLM_CTX_WINDOW=262000
   ```

2. Apply: `docker compose up -d --no-deps openclaw chat core-api`

**Top free models on OpenRouter (with tool calling):**
| Model ID | Context | AA Score | Strengths |
|---|---|---|---|
| `qwen/qwen3-coder:free` | 262K | 28* | **SOTA agentic coding** — comparable to Claude Sonnet 4 |
| `minimax/minimax-m2.5:free` | 197K | 42 | Strong general + coding |
| `google/gemma-4-31b-it:free` | 262K | 39 | Google's open model with reasoning |
| `stepfun/step-3.5-flash:free` | 256K | 38 | Good all-rounder |
| `nvidia/nemotron-3-super-120b-a12b:free` | 262K | 36 | Efficient 120B MoE |
| `openai/gpt-oss-120b:free` | 131K | 33 | OpenAI's open model |
| `meta-llama/llama-3.3-70b-instruct:free` | 65K | — | Solid general model |

*\*Qwen3-Coder's AA Intelligence score (28) is misleadingly low — it's a general metric. On coding-specific benchmarks (SWE-Bench), it matches Claude Sonnet 4.*

**Notes:**
- The 50 req/day limit resets daily (UTC). A one-time $10 top-up permanently raises it to 1,000/day.
- Queue times can increase during peak hours for `:free` models.

---

## Managing the Local LLM (llama-server)

### Stopping the local LLM to save GPU memory

When using an external provider like Gemini, the local llama-server sits idle but still holds ~7 GB of GPU VRAM. Stop it to free that up:

```bash
cd ~/openclaw-local
docker compose stop llama
```

> **Note:** `docker compose stop` keeps the container around (fast to restart) but halts the process and releases the GPU. The `restart: unless-stopped` policy means it will NOT auto-start after a reboot — you must start it manually whenever you want it back.

---

### Switching back to the local LLM

**Step 1 — Start llama-server** (takes ~30–60 seconds to load the model):
```bash
cd ~/openclaw-local
docker compose start llama
# Wait until healthy:
docker compose ps llama   # should show "(healthy)" after ~60s
```

**Step 2 — Update `.env`** to point back to the local server:
```env
# Comment out (or delete) any active external provider block:
# LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
# LLM_API_KEY=AIzaSy...
# LLM_MODEL=gemini-3.1-flash-lite-preview
# LLM_CTX_WINDOW=1048576

# Restore local defaults (these are the fallback values, but being explicit is clearer):
LLM_API_BASE=http://llama:8080
LLM_API_KEY=
LLM_MODEL=Qwen3.5-9B-Q4_K_M
LLM_CTX_WINDOW=32768
```

**Step 3 — Restart the dependent services:**
```bash
docker compose up -d --no-deps chat core-api openclaw
```

**Step 4 — Verify:**
```bash
curl -sf http://localhost:18789/health   # openclaw agent
docker compose logs --tail=20 chat       # should show no API key errors
```

---

### Quick reference

| Goal | Command |
|---|---|
| Free GPU, use Gemini | `docker compose stop llama` |
| Restore local model | `docker compose start llama` |
| Check GPU usage | `nvidia-smi` |
| Check llama health | `docker compose ps llama` |

---

### Local model specs (for reference)

| Property | Value |
|---|---|
| Model | Qwen3.5-9B-Q4_K_M.gguf |
| GPU layers | 33 (full offload) |
| VRAM used | ~7.3 GB |
| Context window | 32,768 tokens |
| Reasoning budget | 2,048 tokens |
| API endpoint (internal) | `http://llama:8080` |

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `LLM error 401` | Bad API key | Re-check `LLM_API_KEY` or `LLM_CODING_KEY` in `.env` |
| `LLM error 429` | Rate limit hit | Wait, or switch to a different model/provider |
| `LLM error 404` | Wrong model name | Check the model ID spelling |
| `Cannot reach LLM` | Bad URL or no internet | Check `LLM_API_BASE` in `.env` |
| openclaw agent not using new model | Env vars not reloaded | `docker compose restart openclaw` |
| Tool calls (`exec`) not working | Model doesn't support function calling | Use Gemini 3 Flash or Qwen3-Coder |
| Coding tasks not routing | Coding env vars not set | Check `LLM_CODING_URL`, `LLM_CODING_KEY`, `LLM_CODING_MODEL` |
| Coding tasks falling back to default | Invalid OpenRouter key | Get key at https://openrouter.ai/keys |
| Chat changes not taking effect | Container not rebuilt | `docker compose build chat && docker compose up -d --no-deps chat` |

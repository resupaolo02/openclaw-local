"""
Chat Service — LLM conversation interface.
Delegates to core-api for skills, sessions, and system-prompt.
Streams completions directly from the LLM (llama-server).
Supports exec tool calls so skills can actually run curl commands.
"""

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import AsyncGenerator, List, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from contextlib import asynccontextmanager

LLM_BASE_URL  = os.getenv("LLM_BASE_URL", "http://llama:8080/v1")  # OpenAI-compat base (e.g. https://api.groq.com/openai/v1)
LLM_API_KEY   = os.getenv("LLM_API_KEY", "")           # set for external APIs (Gemini, Groq, OpenRouter)
LLM_MODEL     = os.getenv("LLM_MODEL", "local")         # model name sent in the API request
# Full completions URL — derived from base so all providers work without extra config
LLM_COMPLETIONS_URL = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

# Coding provider (optional — dedicated coding model via secondary provider like OpenRouter)
LLM_CODING_URL   = os.getenv("LLM_CODING_URL", "")      # e.g. https://openrouter.ai/api/v1/chat/completions
LLM_CODING_KEY   = os.getenv("LLM_CODING_KEY", "")       # secondary provider API key
LLM_CODING_MODEL = os.getenv("LLM_CODING_MODEL", "")     # e.g. qwen/qwen3-coder:free

CORE_API_URL  = os.getenv("CORE_API_URL", "http://core-api:8000")
CORS_ORIGIN   = os.getenv("CORS_ORIGIN", "https://openclaw-frostbite.duckdns.org")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Context budget: keep system prompt + conversation within model's context window
MODEL_CTX_WINDOW  = int(os.getenv("LLM_CTX_WINDOW", "32768"))
MAX_OUTPUT_TOKENS  = 4096   # per-response generation limit
RESERVED_TOKENS   = MAX_OUTPUT_TOKENS + 512  # output + safety margin
MAX_HISTORY_TOKENS = MODEL_CTX_WINDOW - RESERVED_TOKENS
CHARS_PER_TOKEN   = 4       # rough estimate for token counting

logger = logging.getLogger("chat")

_http_client: httpx.AsyncClient | None = None
_coding_client: httpx.AsyncClient | None = None
_core_client: httpx.AsyncClient | None = None

# ── Exec tool definition ─────────────────────────────────────────────────────

EXEC_TOOL = {
    "type": "function",
    "function": {
        "name": "exec",
        "description": (
            "Execute a shell command and return its stdout. "
            "Use this to run curl commands against local services, "
            "read files, or perform any system operation. "
            "Always call this tool instead of describing what you would do."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "The shell command to run (e.g. curl -s http://calendar:9093/api/calendar/week)",
                }
            },
            "required": ["cmd"],
        },
    },
}

MAX_TOOL_TURNS = 10  # allow more tool iterations for complex tasks


@asynccontextmanager
async def lifespan(app):
    global _http_client, _coding_client, _core_client
    llm_headers = {}
    if LLM_API_KEY:
        llm_headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    _http_client = httpx.AsyncClient(headers=llm_headers, timeout=180.0)

    if LLM_CODING_KEY and LLM_CODING_URL:
        _coding_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {LLM_CODING_KEY}"},
            timeout=180.0,
        )
        logger.info("Coding provider ready → URL=%s model=%s", LLM_CODING_URL, LLM_CODING_MODEL)

    _core_client = httpx.AsyncClient(base_url=CORE_API_URL, timeout=15.0)
    logger.info("HTTP clients ready → LLM=%s model=%s, Core=%s", LLM_COMPLETIONS_URL, LLM_MODEL, CORE_API_URL)
    yield
    await _http_client.aclose()
    if _coding_client:
        await _coding_client.aclose()
    await _core_client.aclose()


app = FastAPI(title="Chat Service", lifespan=lifespan)

# ── File extraction ──────────────────────────────────────────────────────────

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".xml",
    ".sh", ".bash", ".zsh", ".fish", ".sql", ".toml", ".ini",
    ".cfg", ".conf", ".log", ".env", ".gitignore",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}


def extract_text_from_file(filename: str, content: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return content.decode("utf-8", errors="replace"), "text"
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [p.extract_text() for p in reader.pages if p.extract_text()]
            if not pages:
                raise ValueError("PDF has no extractable text (may be scanned/image-only)")
            return "\n\n".join(pages), "PDF"
        except ImportError:
            raise ValueError("PDF extraction library not available")
        except Exception as e:
            raise ValueError(f"PDF extraction failed: {e}")
    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            if not paragraphs:
                raise ValueError("DOCX has no extractable text")
            return "\n\n".join(paragraphs), "DOCX"
        except ImportError:
            raise ValueError("DOCX extraction library not available")
        except Exception as e:
            raise ValueError(f"DOCX extraction failed: {e}")
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: plain-text files, .pdf, .docx"
        )


# ── Chat models ──────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    file_text: Optional[str] = None
    file_name: Optional[str] = None


# ── Task classifier for multi-model routing ─────────────────────────────────

_CODING_PATTERNS = [
    # Explicit coding requests
    r'\b(write|create|build|implement|generate|make)\b.{0,40}\b(code|script|function|class|module|program|endpoint|component|api)\b',
    r'\b(code|script|function|class|module|program|endpoint)\b.{0,40}\b(write|create|build|implement|generate|make|for me)\b',
    # Debugging / fixing code
    r'\b(debug|fix|patch|resolve|troubleshoot)\b.{0,40}\b(code|bug|error|issue|crash|exception|problem)\b',
    r'\b(code|bug|error|issue|crash|exception)\b.{0,40}\b(debug|fix|patch|resolve|troubleshoot)\b',
    # Refactoring / review
    r'\b(refactor|optimize|rewrite)\b.{0,30}\b(code|function|class|method|module)\b',
    r'\bcode\s*review\b',
    r'\breview\b.{0,20}\bcode\b',
    # Error patterns from code
    r'\b(syntax|runtime|compile|type|reference|import)\s*error\b',
    r'\b(traceback|stacktrace|stack\s*trace)\b',
    # Language-specific coding requests
    r'\b(write|create|make)\b.{0,30}\b(python|javascript|typescript|bash|html|css|sql|docker|yaml|rust|go|java)\b.{0,20}\b(script|code|function|file|program)\b',
    # Unit tests
    r'\b(unit|integration|write)\s*test',
    # Pull request / code changes
    r'\bpull\s*request\b',
    r'\bmerge\s*conflict\b',
]
_CODING_RE = [re.compile(p, re.IGNORECASE) for p in _CODING_PATTERNS]


def _classify_task(messages: list[dict]) -> str:
    """Classify the last user message as 'coding' or 'default'."""
    last_user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            last_user = msg["content"]
            break
    if not last_user:
        return "default"

    # Code blocks are a strong coding signal
    if "```" in last_user:
        return "coding"

    for pattern in _CODING_RE:
        if pattern.search(last_user):
            return "coding"

    return "default"


# ── LLM streaming ───────────────────────────────────────────────────────────

async def _call_exec(cmd: str) -> str:
    """Execute a shell command via core-api and return the output."""
    if not _core_client:
        return "Error: core-api client not initialised"
    try:
        resp = await _core_client.post("/exec", json={"cmd": cmd}, timeout=30.0)
        data = resp.json()
        output = data.get("output", "")
        exit_code = data.get("exit_code", 0)
        if exit_code != 0 and not output:
            return f"(exit code {exit_code}, no output)"
        return output or "(no output)"
    except Exception as e:
        logger.error("exec via core-api failed: %s", e)
        return f"Error executing command: {e}"


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate (1 token ≈ 4 chars)."""
    return len(text) // CHARS_PER_TOKEN


def _trim_messages_to_budget(messages: list[dict], budget_tokens: int) -> list[dict]:
    """Trim oldest non-system messages to fit within token budget.

    Always keeps the system message (index 0) and the most recent messages.
    Drops oldest user/assistant pairs from the middle when over budget.
    """
    if not messages:
        return messages

    total = sum(_estimate_tokens(m.get("content", "") or "") for m in messages)
    if total <= budget_tokens:
        return messages

    # Always keep system message and at least the last 2 messages
    system_msg = messages[0] if messages[0]["role"] == "system" else None
    conversation = messages[1:] if system_msg else messages[:]
    keep_recent = min(6, len(conversation))  # keep last 6 messages minimum

    while len(conversation) > keep_recent:
        total = _estimate_tokens(system_msg["content"]) if system_msg else 0
        total += sum(_estimate_tokens(m.get("content", "") or "") for m in conversation)
        if total <= budget_tokens:
            break
        conversation.pop(0)

    result = ([system_msg] if system_msg else []) + conversation
    if len(result) < len(messages):
        logger.info("Trimmed conversation from %d to %d messages to fit context", len(messages), len(result))
    return result


async def _stream_llm(messages: list[dict]) -> AsyncGenerator[str, None]:
    if not _http_client:
        yield f"data: {json.dumps({'error': 'HTTP client not initialized'})}\n\n"
        return

    # ── Classify task and select provider ─────────────────────────────────
    task_category = _classify_task(messages)
    if task_category == "coding" and _coding_client and LLM_CODING_URL and LLM_CODING_MODEL:
        active_client = _coding_client
        active_url = LLM_CODING_URL
        active_model = LLM_CODING_MODEL
        logger.info("🔀 Routing to CODING model: %s via %s", active_model, active_url)
    else:
        active_client = _http_client
        active_url = LLM_COMPLETIONS_URL
        active_model = LLM_MODEL
        if task_category == "coding":
            logger.info("Coding task detected but no coding provider configured — using default model")
            task_category = "default"

    # Send model metadata so the UI knows which model is responding
    yield f"data: {json.dumps({'type': 'model_info', 'model': active_model, 'category': task_category})}\n\n"

    current_messages = _trim_messages_to_budget(list(messages), MAX_HISTORY_TOKENS)

    for _turn in range(MAX_TOOL_TURNS):
        payload = {
            "model":       active_model,
            "messages":    current_messages,
            "stream":      True,
            "max_tokens":  MAX_OUTPUT_TOKENS,
            "temperature": 0.7,
            "tools":       [EXEC_TOOL],
            "tool_choice": "auto",
        }

        accumulated_text = ""
        tool_calls: dict[int, dict] = {}
        finish_reason = None

        try:
            async with active_client.stream(
                "POST",
                active_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=180.0,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield f"data: {json.dumps({'error': f'LLM error {resp.status_code}: {body.decode()[:200]}'})}\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                        choice = data["choices"][0]
                        delta = choice.get("delta", {})
                        finish_reason = choice.get("finish_reason") or finish_reason

                        # Stream text content to the user as it arrives
                        if delta.get("content"):
                            accumulated_text += delta["content"]
                            yield f"data: {chunk}\n\n"

                        # Accumulate tool call fragments
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls:
                                tool_calls[idx] = {"id": "", "name": "", "arguments": "", "extra_content": None}
                            if tc.get("id"):
                                tool_calls[idx]["id"] = tc["id"]
                            if tc.get("extra_content"):
                                tool_calls[idx]["extra_content"] = tc["extra_content"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                tool_calls[idx]["name"] += fn["name"]
                            if fn.get("arguments"):
                                tool_calls[idx]["arguments"] += fn["arguments"]
                    except Exception:
                        pass  # malformed chunk; skip

        except httpx.ConnectError:
            yield f"data: {json.dumps({'error': f'Cannot reach LLM at {active_url} — is the LLM server running?'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        except httpx.ReadTimeout:
            yield f"data: {json.dumps({'error': 'LLM response timed out (180s). Try a shorter prompt or simpler question.'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        except Exception as e:
            logger.error("LLM streaming error: %s", e)
            yield f"data: {json.dumps({'error': f'LLM streaming error: {str(e)[:200]}'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # ── Tool call handling ────────────────────────────────────────────
        if tool_calls:
            # Append assistant turn (may have text + tool_calls)
            assistant_msg: dict = {"role": "assistant"}
            if accumulated_text:
                assistant_msg["content"] = accumulated_text
            else:
                assistant_msg["content"] = None
            tc_list = []
            for idx, tc in tool_calls.items():
                entry = {
                    "id":       tc["id"] or f"call_{idx}",
                    "type":     "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                if tc.get("extra_content"):
                    entry["extra_content"] = tc["extra_content"]
                tc_list.append(entry)
            assistant_msg["tool_calls"] = tc_list
            current_messages.append(assistant_msg)

            # Execute each tool and append results
            for idx, tc in tool_calls.items():
                call_id = tc["id"] or f"call_{idx}"
                if tc["name"] == "exec":
                    try:
                        args = json.loads(tc["arguments"])
                        cmd = args.get("cmd", "")
                    except Exception:
                        cmd = tc["arguments"]

                    result = await _call_exec(cmd)
                    logger.info("exec tool: cmd=%s exit_len=%d", cmd[:60], len(result))

                    current_messages.append({
                        "role":         "tool",
                        "tool_call_id": call_id,
                        "name":         "exec",
                        "content":      result,
                    })
                else:
                    # Unknown tool — return empty result and continue
                    current_messages.append({
                        "role":         "tool",
                        "tool_call_id": call_id,
                        "name":         tc["name"],
                        "content":      "Tool not available.",
                    })
            # Loop back to get the final answer
            continue

        # ── Normal completion — done ──────────────────────────────────────
        yield "data: [DONE]\n\n"
        return

    # Exceeded max turns
    overflow_msg = json.dumps({"choices": [{"delta": {"content": "\n\n⚠️ Tool call loop exceeded maximum turns."}, "finish_reason": "stop"}]})
    yield f"data: {overflow_msg}\n\n"
    yield "data: [DONE]\n\n"


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html_path = Path("/app/index.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Chat not found</h1>")


@app.get("/api/skills")
async def skills():
    """Proxy skills from core-api."""
    try:
        resp = await _core_client.get("/skills")
        return resp.json()
    except Exception as e:
        return {"skills": [], "error": str(e)}


@app.get("/api/sessions/list")
async def sessions_list():
    """Proxy sessions list from core-api."""
    try:
        resp = await _core_client.get("/sessions/list")
        return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str):
    """Proxy session messages from core-api."""
    try:
        resp = await _core_client.get(f"/sessions/{session_id}/messages")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Proxy session deletion to core-api."""
    try:
        resp = await _core_client.delete(f"/sessions/{session_id}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/sessions/save")
async def save_session(request: dict):
    """Proxy session save to core-api."""
    try:
        resp = await _core_client.post("/sessions/save", json=request)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Build system prompt from core-api and stream LLM response."""
    try:
        resp = await _core_client.get("/system-prompt")
        system_prompt = resp.json().get("prompt", "You are a helpful assistant.")
    except Exception:
        system_prompt = "You are a helpful assistant."

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    msg_list = [m.model_dump() for m in request.messages]
    if request.file_text and msg_list:
        for i in range(len(msg_list) - 1, -1, -1):
            if msg_list[i]["role"] == "user":
                fname_label = f" ({request.file_name})" if request.file_name else ""
                msg_list[i]["content"] = (
                    f"[Attached file{fname_label}]\n```\n{request.file_text[:12000]}\n```\n\n"
                    + msg_list[i]["content"]
                )
                break
    messages.extend(msg_list)
    return StreamingResponse(
        _stream_llm(messages),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": CORS_ORIGIN,
        },
    )


@app.post("/api/chat/upload")
async def chat_upload(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) // 1024 // 1024}MB). Max: {MAX_UPLOAD_BYTES // 1024 // 1024}MB",
        )
    filename = file.filename or "unknown"
    try:
        text, mime_label = extract_text_from_file(filename, content)
        truncated = len(text) > 15000
        return {
            "filename":   filename,
            "type":       mime_label,
            "text":       text[:15000],
            "truncated":  truncated,
            "char_count": len(text),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction error: {e}")


@app.get("/api/supported-files")
async def supported_files():
    return {
        "supported": {
            "text":     sorted(TEXT_EXTENSIONS),
            "document": [".pdf", ".docx"],
        },
        "not_supported": [".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mp3", ".zip", ".exe"],
        "note": (
            f"The current model ({LLM_MODEL}) may be text-only. "
            "Images, audio, and video are not supported. "
            "For PDF/DOCX, text is extracted and passed as context."
        ),
    }


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

"""
PawChat — Flask backend
Run:  python app.py
      or
      flask run --host=0.0.0.0 --port=5000
"""
import base64
import json
import logging
import logging.handlers
import os

from dotenv import load_dotenv
load_dotenv()

import psutil
import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from database import Database

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

db = Database()

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "llama3.2")

# ---------------------------------------------------------------------------
# Syslog logger — reconfigured at runtime via /api/settings
# ---------------------------------------------------------------------------
syslog_logger = logging.getLogger("pawchat.syslog")
syslog_logger.setLevel(logging.INFO)
_syslog_handler: logging.Handler | None = None


def _reconfigure_syslog():
    """Attach (or detach) a SysLogHandler based on current DB settings."""
    global _syslog_handler
    host = db.get_setting("syslog_host", "")
    port_str = db.get_setting("syslog_port", "514")
    try:
        port = int(port_str)
    except ValueError:
        port = 514

    # Remove old handler
    if _syslog_handler:
        syslog_logger.removeHandler(_syslog_handler)
        _syslog_handler.close()
        _syslog_handler = None

    if host:
        try:
            handler = logging.handlers.SysLogHandler(address=(host, port))
            handler.setFormatter(logging.Formatter("pawchat: %(message)s"))
            syslog_logger.addHandler(handler)
            _syslog_handler = handler
            print(f"[PawChat] Syslog → {host}:{port}")
        except Exception as exc:
            print(f"[PawChat] Syslog config failed: {exc}")


def syslog(event: str, **kwargs):
    """Log a structured event to syslog (no-op if syslog not configured)."""
    if _syslog_handler:
        parts = [f"event={event}"] + [f"{k}={v}" for k, v in kwargs.items()]
        syslog_logger.info(" ".join(parts))


@app.route("/")
def index():
    return render_template("index.html", default_model=DEFAULT_MODEL)

@app.route("/models")
def models_page():
    return render_template("models.html")

@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    return jsonify(db.get_conversations())

@app.route("/api/conversations", methods=["POST"])
def create_conversation():
    data = request.get_json(force=True) or {}
    model = data.get("model", DEFAULT_MODEL)
    conv_id = db.create_conversation(title="New Conversation", model=model)
    return jsonify(db.get_conversation(conv_id)), 201

@app.route("/api/conversations/<int:conv_id>", methods=["GET"])
def get_conversation(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    return jsonify(conv)

@app.route("/api/conversations/<int:conv_id>", methods=["PATCH"])
def update_conversation(conv_id):
    data = request.get_json(force=True) or {}
    db.update_conversation(
        conv_id,
        title=data.get("title"),
        model=data.get("model"),
        system_prompt=data.get("system_prompt"),
        web_search_enabled=data.get("web_search_enabled"),
    )
    return jsonify(db.get_conversation(conv_id))

@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    db.delete_conversation(conv_id)
    return jsonify({"ok": True})

@app.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
def get_messages(conv_id):
    return jsonify(db.get_messages(conv_id))

def _web_search(query: str, max_results: int = 4) -> list:
    """Return DuckDuckGo search results for query."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    conv_id = data.get("conversation_id")
    user_message = data.get("message", "").strip()
    model = data.get("model", DEFAULT_MODEL)

    if not conv_id or not user_message:
        return jsonify({"error": "conversation_id and message are required"}), 400

    if db.message_count(conv_id) == 0:
        title = user_message[:60] + ("..." if len(user_message) > 60 else "")
        db.update_conversation(conv_id, title=title)

    db.add_message(conv_id, "user", user_message)
    syslog("chat_message", conv_id=conv_id, model=model, role="user", chars=len(user_message))
    history = db.get_messages(conv_id)

    # Build Ollama message list
    ollama_messages = []

    # Inject system prompt if set
    conv = db.get_conversation(conv_id)
    system_prompt = (conv or {}).get("system_prompt") or ""
    web_search_enabled = bool((conv or {}).get("web_search_enabled"))

    if system_prompt:
        ollama_messages.append({"role": "system", "content": system_prompt})

    # Inject web search results when enabled
    if web_search_enabled:
        results = _web_search(user_message)
        if results:
            search_ctx = "Web search results (use these to inform your answer):\n" + "\n".join(
                f"- {r.get('title', '')}: {r.get('body', '')} ({r.get('href', '')})"
                for r in results
            )
            ollama_messages.append({"role": "system", "content": search_ctx})

    # Inject attached files as context before history
    files = db.get_conversation_files(conv_id)
    if files:
        text_files = [f for f in files if not f["mimetype"].startswith("image/")]
        if text_files:
            file_ctx = "Attached files for context:\n" + "\n\n".join(
                f"=== {f['filename']} ===\n{_get_file_content(conv_id, f['id'])}"
                for f in text_files
            )
            ollama_messages.append({"role": "system", "content": file_ctx})

    # Add conversation history; attach images to the last user message if present
    image_files = [f for f in files if f["mimetype"].startswith("image/")] if files else []
    for i, m in enumerate(history):
        msg = {"role": m["role"], "content": m["content"]}
        # Attach images to the most recent user message only
        if m["role"] == "user" and i == len(history) - 1 and image_files:
            images_b64 = []
            for imgf in image_files:
                raw = db.get_conversation_file(imgf["id"], conv_id)
                if raw:
                    images_b64.append(raw["content"])
            if images_b64:
                msg["images"] = images_b64
        ollama_messages.append(msg)

    def generate():
        full_response = []
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/chat",
                json={"model": model, "messages": ollama_messages, "stream": True},
                stream=True, timeout=180,
            )
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    yield f"data: {json.dumps({'error': chunk['error'], 'done': True})}\n\n"
                    return
                token = chunk.get("message", {}).get("content", "")
                if token:
                    full_response.append(token)
                    yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
                if chunk.get("done"):
                    complete = "".join(full_response)
                    db.add_message(conv_id, "assistant", complete)
                    syslog("chat_response", conv_id=conv_id, model=model, role="assistant", chars=len(complete))
                    updated_conv = db.get_conversation(conv_id)
                    yield f"data: {json.dumps({'done': True, 'conversation': updated_conv})}\n\n"
                    return
        except requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'error': 'Cannot connect to Ollama at ' + OLLAMA_HOST, 'done': True})}\n\n"
        except requests.exceptions.Timeout:
            yield f"data: {json.dumps({'error': 'Ollama request timed out.', 'done': True})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc), 'done': True})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _get_file_content(conv_id, file_id):
    """Return raw text content of a stored file."""
    row = db.get_conversation_file(file_id, conv_id)
    return row["content"] if row else ""

@app.route("/api/models", methods=["GET"])
def list_models():
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama", "models": []}), 503
    except Exception as exc:
        return jsonify({"error": str(exc), "models": []}), 500

@app.route("/api/models/running", methods=["GET"])
def running_models():
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/ps", timeout=5)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama", "models": []}), 503
    except Exception as exc:
        return jsonify({"error": str(exc), "models": []}), 500

@app.route("/api/ollama/status", methods=["GET"])
def ollama_status():
    try:
        requests.get(f"{OLLAMA_HOST}/", timeout=3)
        return jsonify({"online": True, "host": OLLAMA_HOST})
    except Exception:
        return jsonify({"online": False, "host": OLLAMA_HOST}), 503

@app.route("/api/models/pull", methods=["POST"])
def pull_model():
    """Stream a model download. SSE events with progress %."""
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    def generate():
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/pull",
                json={"name": name, "stream": True},
                stream=True, timeout=3600,
            )
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    yield f"data: {json.dumps({'error': chunk['error'], 'done': True})}\n\n"
                    return
                status = chunk.get("status", "")
                total = chunk.get("total", 0)
                completed = chunk.get("completed", 0)
                pct = round((completed / total) * 100) if total else 0
                done = status == "success"
                yield f"data: {json.dumps({'status': status, 'pct': pct, 'total': total, 'completed': completed, 'done': done})}\n\n"
                if done:
                    return
        except requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'error': 'Cannot connect to Ollama', 'done': True})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc), 'done': True})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/models/<path:model_name>", methods=["DELETE"])
def delete_model(model_name):
    try:
        r = requests.delete(f"{OLLAMA_HOST}/api/delete", json={"name": model_name}, timeout=30)
        r.raise_for_status()
        return jsonify({"ok": True, "deleted": model_name})
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama"}), 503
    except requests.exceptions.HTTPError as exc:
        return jsonify({"error": str(exc)}), exc.response.status_code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/models/load", methods=["POST"])
def load_model():
    """Pre-warm a model into VRAM. keep_alive: '10m', '1h', or -1 for infinite."""
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    keep_alive = data.get("keep_alive", "10m")
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate",
                          json={"model": name, "keep_alive": keep_alive, "prompt": ""},
                          timeout=60)
        r.raise_for_status()
        return jsonify({"ok": True, "model": name, "keep_alive": keep_alive})
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/models/unload", methods=["POST"])
def unload_model():
    """Evict a model from VRAM immediately."""
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate",
                          json={"model": name, "keep_alive": 0, "prompt": ""},
                          timeout=30)
        r.raise_for_status()
        return jsonify({"ok": True, "model": name})
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/models/show", methods=["POST"])
def show_model():
    """Detailed model info: modelfile, parameters, template, license."""
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/show", json={"name": name}, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(db.get_all_settings())


@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.get_json(force=True) or {}
    for key, value in data.items():
        db.set_setting(str(key), str(value))
    # Reconfigure syslog if relevant keys changed
    if "syslog_host" in data or "syslog_port" in data:
        _reconfigure_syslog()
        syslog("settings_updated", keys=",".join(data.keys()))
    return jsonify({"ok": True})


@app.route("/api/system/stats", methods=["GET"])
def system_stats():
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    return jsonify({
        "cpu_percent": cpu,
        "ram_used_gb": round(ram.used / 1e9, 1),
        "ram_total_gb": round(ram.total / 1e9, 1),
        "ram_percent": ram.percent,
    })


@app.route("/api/conversations/<int:conv_id>/files", methods=["GET"])
def list_files(conv_id):
    return jsonify(db.get_conversation_files(conv_id))


@app.route("/api/conversations/<int:conv_id>/files", methods=["POST"])
def upload_file(conv_id):
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    filename = f.filename or "upload"
    mimetype = f.mimetype or "application/octet-stream"

    if mimetype.startswith("image/"):
        # Store as base64 string (Ollama expects raw base64, no data URI prefix)
        content = base64.b64encode(f.read()).decode("utf-8")
    else:
        # Store as decoded text (UTF-8)
        try:
            content = f.read().decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "File must be UTF-8 encoded text or an image"}), 400

    file_id = db.add_conversation_file(conv_id, filename, mimetype, content)
    return jsonify({
        "id": file_id,
        "conversation_id": conv_id,
        "filename": filename,
        "mimetype": mimetype,
    }), 201


@app.route("/api/conversations/<int:conv_id>/files/<int:file_id>", methods=["DELETE"])
def delete_file(conv_id, file_id):
    db.delete_conversation_file(file_id, conv_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    db.init()
    _reconfigure_syslog()
    syslog("startup", host=OLLAMA_HOST)
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print(f"[PawChat] http://0.0.0.0:{port}")
    print(f"[PawChat] Ollama: {OLLAMA_HOST}")
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)

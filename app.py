"""
Ollama Chat — Flask backend
Run:  python app.py
      or
      flask run --host=0.0.0.0 --port=5000
"""
import json
import os

import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from database import Database

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

db = Database()

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "llama3.2")


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
    db.update_conversation(conv_id, title=data.get("title"), model=data.get("model"))
    return jsonify(db.get_conversation(conv_id))

@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    db.delete_conversation(conv_id)
    return jsonify({"ok": True})

@app.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
def get_messages(conv_id):
    return jsonify(db.get_messages(conv_id))

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
    history = db.get_messages(conv_id)
    ollama_messages = [{"role": m["role"], "content": m["content"]} for m in history]

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
                    conv = db.get_conversation(conv_id)
                    yield f"data: {json.dumps({'done': True, 'conversation': conv})}\n\n"
                    return
        except requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'error': 'Cannot connect to Ollama at ' + OLLAMA_HOST, 'done': True})}\n\n"
        except requests.exceptions.Timeout:
            yield f"data: {json.dumps({'error': 'Ollama request timed out.', 'done': True})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc), 'done': True})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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


if __name__ == "__main__":
    db.init()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print(f"[Ollama Chat] http://0.0.0.0:{port}")
    print(f"[Ollama Chat] Ollama: {OLLAMA_HOST}")
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)

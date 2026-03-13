# 🦙 Ollama Chat

A polished, self-hosted chat interface for [Ollama](https://ollama.ai/) — built with Flask, streamed responses, syntax-highlighted code artifacts, and a real-time model load monitor.

---

## ✨ Features

| Feature | Details |
|---|---|
| **Chat interface** | Sidebar conversation list, grouped by date (Today / Yesterday / Older) |
| **Streaming responses** | Tokens stream token-by-token using Server-Sent Events (SSE) |
| **Code artifacts** | Code blocks render with macOS-style headers, language badges, syntax highlighting (Highlight.js), and a one-click Copy button |
| **Model selector** | Switch models per conversation from a dropdown populated by your local Ollama installation |
| **GPU / model load monitor** | Sidebar panel shows every currently-loaded model with its VRAM consumption, polling every 8 seconds |
| **Persistent conversations** | Full conversation history stored in SQLite (zero config) — switch to PostgreSQL with one env var |
| **Stop generation** | Cancel an in-flight response at any time |
| **Markdown rendering** | Full GFM markdown — tables, bold, italic, blockquotes, lists, headers |

---

## 📁 Project Structure

```
ollama-chat/
├── app.py              ← Flask application + API routes
├── database.py         ← DB abstraction (SQLite / PostgreSQL)
├── requirements.txt    ← Python dependencies
├── .env.example        ← Environment variable reference
├── README.md
└── templates/
    └── index.html      ← Single-file frontend (HTML + CSS + JS)
```

---

## 🚀 Quick Start

### 1 · Prerequisites

- **Python 3.10+**
- **Ollama** running locally: https://ollama.ai/download
- At least one model pulled:

```bash
ollama pull llama3.2
```

### 2 · Install dependencies

```bash
cd ollama-chat
pip install -r requirements.txt
```

### 3 · Configure (optional)

Copy the example env file and edit as needed:

```bash
cp .env.example .env
```

The defaults work out-of-the-box if Ollama is on `localhost:11434`.

### 4 · Run

```bash
python app.py
```

Then open **http://localhost:5000** in your browser.

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API base URL |
| `DEFAULT_MODEL` | `llama3.2` | Model selected on startup |
| `PORT` | `5000` | Flask listen port |
| `FLASK_DEBUG` | `1` | Set to `0` for production |
| `DB_TYPE` | `sqlite` | `sqlite` or `postgresql` |
| `DATABASE_URL` | `chat.db` | SQLite file path **or** PostgreSQL DSN |

---

## 🗄️ Database

### SQLite (default)

No configuration needed. A `chat.db` file is created automatically in the project directory on first run.

### Migrating to PostgreSQL

1. Install the driver:

```bash
pip install psycopg2-binary
```

2. Set environment variables:

```bash
DB_TYPE=postgresql
DATABASE_URL=postgresql://user:password@localhost:5432/ollama_chat
```

3. Create the target database in Postgres, then restart the app — the schema is created automatically.

> **Note:** The `database.py` abstraction layer uses the same SQL for both backends. The only differences are the placeholder style (`?` vs `%s`), auto-increment syntax, and timestamp functions — all handled transparently by the `Database` class.

---

## 🔌 API Reference

All endpoints return JSON.

### Conversations

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/conversations` | List all conversations (newest first) |
| `POST` | `/api/conversations` | Create a new conversation — body: `{"model": "llama3.2"}` |
| `GET` | `/api/conversations/:id` | Get a single conversation |
| `PATCH` | `/api/conversations/:id` | Update title or model — body: `{"title": "...", "model": "..."}` |
| `DELETE` | `/api/conversations/:id` | Delete conversation + all its messages |

### Messages

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/conversations/:id/messages` | List all messages in a conversation |

### Chat (Streaming)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Send a message and stream the response via SSE |

**Request body:**
```json
{
  "conversation_id": 1,
  "message": "Hello!",
  "model": "llama3.2"
}
```

**SSE event format:**
```
data: {"token": "Hello", "done": false}
data: {"token": " there", "done": false}
data: {"done": true, "conversation": {...}}
```

### Models / Ollama

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/models` | List all locally available Ollama models |
| `GET` | `/api/models/running` | List currently loaded models with VRAM usage |
| `GET` | `/api/ollama/status` | Health-check for the Ollama service |

---

## 🐳 Running with Docker (optional)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
ENV FLASK_DEBUG=0
CMD ["python", "app.py"]
```

```bash
docker build -t ollama-chat .
docker run -p 5000:5000 \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  -v $(pwd)/chat.db:/app/chat.db \
  ollama-chat
```

> Use `host.docker.internal` to reach Ollama running on the Docker host (Mac/Windows). On Linux, use `--network=host` and `OLLAMA_HOST=http://localhost:11434`.

---

## 🛣️ Roadmap / Future Ideas

- [ ] Conversation search
- [ ] System prompt / persona per conversation
- [ ] File / image attachment (multimodal models)
- [ ] Export conversation as Markdown or PDF
- [ ] User authentication (for multi-user deployments)
- [ ] PostgreSQL migration script from SQLite
- [ ] Dark / light theme toggle
- [ ] Token count display per message

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'Add my feature'`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request

---

## 📄 License

MIT — do whatever you like with it.

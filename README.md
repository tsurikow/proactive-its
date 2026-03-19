# proactive-its

Baseline proactive tutoring backend for Calculus I:
- resume-aware tutor flow (`/v1/start`, `/v1/lesson/current`, `/v1/next`)
- grounded Q&A with RAG (`/v1/chat`)
- evidence-backed mastery updates (`/v1/feedback`)
- React/Vite frontend (`/frontend`) with chat + plan + LaTeX rendering

## Stack
- Python 3.13 + `uv`
- FastAPI + Uvicorn
- PostgreSQL + SQLAlchemy async + Alembic
- Qdrant
- OpenRouter (chat generation)
- LangChain LCEL (`langchain-core`, `langchain-openai`)
- OpenAI-compatible embeddings endpoint (Ollama/OpenAI/OpenRouter-compatible)

## Quick start
1. Create env file:
```bash
cp .env.example .env
```

2. Fill `.env`:
- `OPENROUTER_API_KEY` (required for tutor flow content)
- `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`
- `BOOK_JSON_PATH` (path to local `book.json`)

3. Install dependencies:
```bash
uv sync
```

4. Start services:
```bash
docker compose up --build
```
This brings up Postgres and Qdrant, runs the bootstrap container once, then starts the API.

Fresh local reset:
```bash
./scripts/dev-reset-up.sh
```
This reset removes the Postgres volume, reruns bootstrap, and keeps the Qdrant volume intact.

## Bootstrap and migrations
- FastAPI startup does not run migrations.
- The bootstrap flow applies the single baseline Alembic revision and seeds the default plan template.

Schema only:
```bash
uv run alembic upgrade head
```

Full local bootstrap:
```bash
uv run python scripts/bootstrap_runtime.py
```

## Index content
```bash
uv run python scripts/index_content.py --documents /path/to/documents.jsonl --recreate
```

Embedding budget preflight:
```bash
uv run python scripts/check_embedding_budget.py --documents /path/to/documents.jsonl
```

Expected output counters:
- `docs`
- `parents`
- `children`

## Export OpenAPI
```bash
uv run python scripts/export_openapi.py
```

## Frontend
The frontend is in `frontend/` and uses React + Vite + Tailwind CSS v4.

1. Run backend API first (`http://localhost:8000`).
2. Start frontend:
```bash
cd frontend
npm install
npm run generate:api
npm run dev
```
3. Open `http://localhost:5173`.

Frontend env:
- `VITE_API_BASE_URL` (default: `/v1`, proxied by Vite to `http://localhost:8000`)

## API
Base URL: `http://localhost:8000`

Health:
```bash
curl http://localhost:8000/v1/health
```

Start:
```bash
curl -X POST http://localhost:8000/v1/start \
  -H "Content-Type: application/json" \
  -d '{"learner_id":"demo-student"}'
```

Current lesson:
```bash
curl "http://localhost:8000/v1/lesson/current?learner_id=demo-student"
```

Chat:
```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "learner_id":"demo-student",
    "message":"What is a function?",
    "context": {},
    "mode":"tutor"
  }'
```

Feedback:
```bash
curl -X POST http://localhost:8000/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"learner_id":"demo-student","interaction_id":1,"confidence":4}'
```

Next section:
```bash
curl -X POST http://localhost:8000/v1/next \
  -H "Content-Type: application/json" \
  -d '{"learner_id":"demo-student","force":false}'
```

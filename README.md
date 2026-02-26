# proactive-its

Baseline proactive tutoring backend for Calculus I:
- resume-aware tutor flow (`/v1/start`, `/v1/lesson/current`, `/v1/next`)
- grounded Q&A with RAG (`/v1/chat`)
- simple mastery updates (`/v1/feedback`)
- React/Vite frontend (`/frontend`) with chat + plan + LaTeX rendering

## Stack
- Python 3.13 + `uv`
- FastAPI + Uvicorn
- PostgreSQL + SQLAlchemy async + Alembic
- Qdrant
- OpenRouter (chat generation)
- LangChain LCEL (`langchain-core`, `langchain-openai`, `langchain-qdrant`)
- OpenAI-compatible embeddings endpoint (Ollama/OpenAI/OpenRouter-compatible)

## Parent-child indexing
Two Qdrant collections are used in one ingest pass:

1. `calc1_sections` (parents)
- one point per section/document
- stores full cleaned section text (`content_text_full`)
- used for section-level tutor packaging

2. `calc1_chunks` (children)
- semantic chunks per section
- used for retrieval/citations in `/v1/chat`
- includes metadata like `chunk_type`, `subsection_title`, `order_index`

Default child chunk policy:
- target: `900` tokens
- overlap: `120` tokens
- split preference: heading/labeled blocks/paragraph fallback

## Tutor flow behavior
- `/v1/start`
  - ensures learner and stage state
  - loads pre-generated default plan template
  - returns tutor intro + current stage/progress summary (fast path)
- `/v1/lesson/current`
  - returns current stage lesson package
  - generates lesson from full parent section markdown (`content_text_full`) in one LLM pass
  - preserves figures/links/tables/non-prose blocks in-order with validation checks
  - uses cache when source hash + generator version match
  - returns `503` when LLM generation or preservation validation fails
- `/v1/next`
  - manually advances one stage
  - returns next stage info (no full lesson body)
- lesson package (`lesson`) includes:
  - `lesson_steps[]` with `source_chunk_ids`
  - `section_summary_md`

## Grounded chat behavior
`/v1/chat` retrieves from `calc1_chunks` only and enforces strict grounding:
- dense retrieval (`RAG_TOP_K_FETCH=24`) + lightweight rerank (`RAG_FINAL_K=6`)
- weak-evidence refusal when score/evidence thresholds are not met
- citations must be from retrieved chunk ids only
- no deterministic answer fallback for malformed model output (returns 503 after repair attempt)

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

Development reset (ephemeral DB + rebuild):
```bash
./scripts/dev-reset-up.sh
```
This reset keeps Qdrant vectors and recreates Postgres state only.

## Frontend (React + Vite)
The frontend is in `/Users/bigboss/PycharmProjects/proactive-its/frontend`.
It uses React + Vite + Tailwind CSS v4.

1. Run backend API first (`http://localhost:8000`).
2. Start frontend:
```bash
cd frontend
npm install
npm run dev
```
3. Open `http://localhost:5173`.

Frontend env:
- `VITE_API_BASE_URL` (default: `/v1`, proxied by Vite to `http://localhost:8000`)

The UI supports:
- learner ID gate (stored in localStorage key `its.learner_id`)
- Start -> current lesson -> next stage flow
- grounded chat
- Markdown + LaTeX rendering with KaTeX
- study plan side panel

## Migrations
- App startup runs `alembic upgrade head`.
- Manual:
```bash
uv run alembic revision -m "change"
uv run alembic upgrade head
```

## Index content
```bash
uv run python scripts/index_content.py --documents /path/to/documents.jsonl --recreate
```

Expected output counters:
- `docs`
- `parents`
- `children`

Embedding budget preflight:
```bash
uv run python scripts/check_embedding_budget.py --documents /path/to/documents.jsonl
```

This reports longest parent sections and longest child chunks.

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

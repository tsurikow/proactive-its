# proactive-its

Baseline tutoring system with deterministic study flow and RAG Q&A.

## Stack
- Python 3.13+
- `uv` for env/package management
- FastAPI + Uvicorn
- Qdrant
- OpenRouter (LLM)
- Ollama or OpenAI-compatible embeddings endpoint

## Quick Start
1. Copy env file:
   - `cp .env.example .env`
2. Set keys in `.env`:
   - `OPENROUTER_API_KEY` (required for `/v1/start` and tutor content)
   - `EMBEDDING_API_KEY`
   - `BOOK_JSON_PATH` (path to your `book.json`)
3. Install dependencies:
   - `uv sync`
4. Start services:
   - `docker compose up --build`

## Index Content

```bash
uv run python scripts/index_content.py --documents /path/to/documents.jsonl --recreate
```

## API
Base URL: `http://localhost:8000`

- `POST /v1/start`
- `POST /v1/next`
- `POST /v1/chat`
- `POST /v1/feedback`

Health check:

```bash
curl http://localhost:8000/v1/health
```

## Behavior Summary
- `/v1/start` uses the LLM to introduce itself, present a plan outline, and provide the first lesson.
- `current_item` includes:
  - `content_text`: raw chunk text from the source
  - `content_tutor`: LLM-generated lesson based only on that chunk
- `/v1/next` advances through chunks within the current section, then moves to the next section.
- `/v1/chat` answers questions without advancing progress.
- `/v1/feedback` updates mastery and can auto-advance on high confidence.

## Example Flow

1. Start tutoring session:

```bash
curl -X POST http://localhost:8000/v1/start \
  -H "Content-Type: application/json" \
  -d '{"learner_id":"demo-student"}'
```

2. Ask a question:

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

3. Send feedback:

```bash
curl -X POST http://localhost:8000/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"learner_id":"demo-student","interaction_id":1,"confidence":4}'
```

4. Advance:

```bash
curl -X POST http://localhost:8000/v1/next \
  -H "Content-Type: application/json" \
  -d '{"learner_id":"demo-student","force":false}'
```

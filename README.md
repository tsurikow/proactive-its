# Proactive ITS

An LLM-first intelligent tutoring system. A teacher AI drives the session loop — it reads learner state, reasons over the curriculum, and decides what to teach, ask, or repair — using structured generation at every decision point.

---

## Navigation

- [Architecture overview](#architecture-overview)
- [SGR — Schema-Guided Reasoning](#sgr--schema-guided-reasoning)
- [Learner memory](#learner-memory)
- [Mastery tracking](#mastery-tracking)
- [Async infrastructure: Celery · RabbitMQ · Redis](#async-infrastructure-celery--rabbitmq--redis)
- [Stack & libraries](#stack--libraries)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Data & indexing](#data--indexing)
- [Deployment](#deployment)

---

## Architecture overview

```
Browser (React + Vite)
     │  HTTP / cookie auth
     ▼
FastAPI  ──►  Teacher Runtime  ──►  TeacherEngine (SGR calls)
     │              │                      │
     │         State services         OpenRouter API
     │         (Postgres)             (PydanticAI)
     │
     ├──►  Chat Service (durable path)
     │         │
     │    RabbitMQ ──► Celery Worker ──► Teacher Runtime
     │         │
     │       Redis  (pub/sub turn notification)
     │
     └──►  RAG pipeline
               │
            Qdrant (vector search)
```

The teacher owns the session. The learner sends events — message, answer, navigation signal — and the teacher responds with a pedagogical action: explain, ask, assign, propose a move. All decisions pass through typed SGR schemas; there are no free-form agent tool calls.

---

## SGR — Schema-Guided Reasoning

SGR (Schema-Guided Reasoning) is the core technique for structured LLM inference. Instead of asking the model to output free text, each decision type has a dedicated Pydantic schema where **field order encodes the reasoning chain**. The model fills reasoning fields first, then decision fields, then output fields. This eliminates hallucinated actions and makes every decision inspectable.

Reference: [abdullin.com/schema-guided-reasoning](https://abdullin.com/schema-guided-reasoning/)

### The six schemas

Each teacher turn runs 2–5 SGR calls depending on the event type. All calls use `NativeOutput(strict=True)` via PydanticAI, meaning the model cannot produce output that fails schema validation.

| Schema | Purpose | Temp |
|---|---|---|
| `IntentAndRoute` | Classify the learner's message (navigation / answer / question / signal) and pick the response strategy (RAG reply / pedagogical reply / clarify first) | 0.0 |
| `TeacherTurn` | Decide the teacher's action (teach / ask / assign / propose) and write the message | 0.35 |
| `AnswerEvaluation` | Verify a learner answer against the pending task: `correct / partial / incorrect / unresolved / skipped` | 0.0 |
| `WeakAnswerPlan` | Plan recovery after a wrong or partial answer: explain, hint, or reask | 0.35 |
| `SectionUnderstanding` | Analyse a textbook section once and cache the result per section + content hash | 0.0 |
| `LearnerMemory` | Synthesise or update the persistent learner model: strengths, misconceptions, pace, recommendations | 0.1 |

### Schema structure example

```python
class IntentAndRoute(SGRSchema):
    # 1. Reasoning — LLM fills these first
    message_read: str          # restate what the learner said
    context_read: str          # what is the current stage / task
    intent_evidence: str       # evidence for the classification

    # 2. Decision
    intent_type: LearnerTurnIntentType
    route_type: InteractionRouteType

    # 3. Optional output
    navigation_action: LearnerNavigationAction | None
    retrieval_aim: str | None
```

Lower temperature for classification and evaluation (deterministic), higher for message generation (natural variation).

---

## Learner memory

Memory is a persistent JSON blob per learner × curriculum template, synthesised periodically by the `LearnerMemory` SGR call and stored in Postgres.

**What it tracks:**

| Field | Description |
|---|---|
| `strengths` | Topics the learner has demonstrated solid understanding of |
| `misconceptions` | Recurring errors or misunderstandings |
| `pace_observation` | How quickly the learner moves through material |
| `engagement_level` | Observed participation and response quality |
| `learning_debt_summary` | Topics skipped, refused, or left unresolved |
| `teaching_recommendations` | What to prioritise in the next session |
| `priority_revisit_topics` | Specific sections that need revisiting |

**Write path:** Memory synthesis is triggered every N learner turns and on session start/end events. With `DURABLE_CHAT_ENABLED=true` (the default), synthesis is enqueued as a Celery task with retry/backoff — it survives process crashes. Otherwise it runs as a fire-and-forget `asyncio.create_task`.

**Read path:** Memory is loaded at session start and injected into every SGR prompt as context, so the teacher always knows who it is talking to.

In addition to the narrative memory, the system maintains per-section mastery snapshots — see [Mastery tracking](#mastery-tracking) below.

---

## Mastery tracking

Every checkpoint or exercise answer flows through the `AnswerEvaluation` SGR schema and immediately updates the learner's mastery score. There is no separate feedback endpoint — all assessment data comes from the teacher's in-session evaluation.

### Data flow

```
Learner answer
     │
     ▼
AnswerEvaluation (SGR)
     │  status: correct / partial / incorrect / unresolved / skipped
     │  confidence: 0.0–1.0 (model's certainty in the verdict)
     ▼
compute_mastery_delta()
     │  inputs: status, model_confidence, attempt_count, current_mastery
     ▼
TopicEvidence row  ──►  MasterySnapshot  ──►  TopicProgressProjection
     │                        │
     ▼                        ▼
AdaptationContext         Plan payload
(stage_signal,            (mastery_score
 weak/strong topics,       per section)
 recent_pattern)
```

### Delta formula

```
base = { correct: +0.25, partial: +0.10, incorrect: −0.12, unresolved: −0.03, skipped: −0.05 }

delta = base[status]
      × (0.5 + 0.5 × model_confidence)       # scale by model certainty
      × (1.0 − current_mastery × 0.6)         # diminishing returns (positive only)
      × attempt_penalty                        # correct on 3rd try ≠ correct on 1st
```

**Attempt penalty** (applied only when `status = correct` and `attempt_count > 1`): `max(0.3, 1.0 − 0.2 × (attempt_count − 1))`.

### Mastery lifecycle

| Concept | Description |
|---|---|
| **MasterySnapshot** | Per-section raw score (0.0–1.0), evidence count, last assessment decision. Written on every evaluation. |
| **Effective mastery** | Raw score × decay multiplier. Used for all runtime decisions. |
| **Decay** | Half-life model. After a configurable grace period, mastery decays toward zero to encourage revisiting. Controlled by `MASTERY_DECAY_ENABLED`, `MASTERY_DECAY_GRACE_PERIOD_HOURS`, `MASTERY_DECAY_HALF_LIFE_DAYS`. |
| **Stage signal** | Derived from effective mastery and last assessment: `new` → `needs_support` → `progressing` → `ready`. Injected into every SGR prompt. |
| **AdaptationContext** | Aggregates current topic mastery, weak/strong related topics, module summary, and recent evidence pattern. Built fresh each turn and passed to the teacher. |
| **Completed threshold** | Section is marked `completed` when effective mastery ≥ 0.8. |

### Unified assessment vocabulary

All evidence uses `CheckpointEvaluationStatus` values: `correct`, `partial`, `incorrect`, `unresolved`, `skipped`. These are the same values produced by the `AnswerEvaluation` SGR schema and stored in `TopicEvidence.assessment_decision`.

---

## Async infrastructure: Celery · RabbitMQ · Redis

The teacher runtime is compute-heavy (3–5 LLM calls per turn, up to 300 s total). Running that synchronously in a FastAPI request would block the web server.

```
POST /v1/teacher/session
  │
  ├── [fast path, durable_chat_enabled=false]
  │     Run LLM calls inline in the request (simple, no queue)
  │
  └── [durable path, durable_chat_enabled=true]
        1. Write a pending TurnRecord to Postgres
        2. Enqueue task_id → RabbitMQ
        3. Return 202 with task_id
        4. Client subscribes to SSE: GET /v1/teacher/session/stream/{task_id}
        5. API waits on Redis pub/sub channel `chat_turn:{turn_id}`
        6. Celery worker picks up the task, runs teacher runtime
        7. Worker publishes "done" to the Redis channel
        8. API unblocks, reads the completed TurnRecord, returns it
```

**RabbitMQ** — durable task broker. Tasks survive worker restarts. The `chat_generation` queue routes to the teacher worker pool.

**Celery** — worker framework. Two registered tasks:
- `run_chat_generation` — executes a full teacher session turn (max 3 retries with exponential backoff)
- `run_memory_synthesis` — synthesises learner memory asynchronously (max 2 retries)

**Redis** — two roles:
1. Pub/sub notification channel so the API process knows exactly when a turn completes, without polling Postgres.
2. Embedding cache (`redis_cache_ttl_seconds`, default 24 h) — avoids re-computing embeddings for repeated queries.

---

## Stack & libraries

### Backend

| Library | Role |
|---|---|
| **FastAPI** | HTTP framework, OpenAPI schema generation, cookie-based auth middleware |
| **PydanticAI** | Agent framework — runs SGR calls against OpenRouter with `NativeOutput(strict=True)` |
| **Pydantic v2** | Data validation and SGR schema definitions; `ConfigDict(extra="forbid")` enforces strict output |
| **pydantic-settings** | Typed `.env` configuration with validation |
| **OpenAI SDK** | Used as the HTTP client for OpenRouter (compatible API) |
| **SQLAlchemy 2 (async)** | ORM for Postgres; all queries are `async` via `asyncpg` |
| **asyncpg** | Low-level async Postgres driver |
| **Alembic** | Database migrations |
| **Qdrant client** | Vector store for RAG — stores section chunks and section-level embeddings |
| **Celery** | Distributed task worker for durable chat turns and memory synthesis |
| **redis[hiredis]** | Pub/sub notification + embedding cache |
| **tiktoken** | Token counting for chunk sizing and context trimming |
| **markdown-it-py** | Markdown processing for content indexing |
| **pwdlib[argon2]** | Password hashing (Argon2) |
| **itsdangerous** | Signed session tokens for password reset links |
| **logfire** | Optional structured observability (FastAPI + SQLAlchemy integration) |
| **uv** | Fast Python package manager and project tool |

### Frontend

| Library | Role |
|---|---|
| **React 19** | UI framework |
| **Vite 7** | Build tool and dev server |
| **TypeScript** | Type safety across the frontend |
| **TanStack Query** | Server state management — session, readiness polling, mutations |
| **Tailwind CSS 4** | Utility-first styling |
| **react-markdown** | Renders teacher messages (Markdown) |
| **remark-math / rehype-katex** | LaTeX math rendering in messages |
| **remark-gfm** | GitHub Flavored Markdown tables, task lists |
| **lucide-react** | Icon set |
| **pnpm** | Package manager |
| **openapi-typescript** | Generates `types/generated/api.ts` from the FastAPI OpenAPI spec |

---

## Quick start

### Prerequisites

- Docker + Docker Compose
- `uv` — [install](https://docs.astral.sh/uv/getting-started/installation/)
- `pnpm` — for local frontend dev only

### 1. Environment

```bash
cp .env.example .env
```

Required values in `.env`:

```bash
OPENROUTER_API_KEY=...
POSTGRES_PASSWORD=...
RABBITMQ_DEFAULT_PASS=...
AUTH_SECRET_KEY=...       # any long random string
```

### 2. Install local tooling (for codegen and DVC)

```bash
uv sync --extra dev
cd frontend && pnpm install && pnpm run generate:api && cd ..
```

### 3. Pull versioned source data

```bash
uv run --extra dev dvc pull
```

### 4. Start the stack

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

### 5. Index content (first run only)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm indexer
```

### 6. Verify

```bash
curl http://localhost:8000/v1/health/ready
# {"status":"ready", ...}
```

Open `http://localhost` in your browser — signup, login, start a session.

> **Note:** `GET /v1/health/ready` returns `503` until indexing completes. `GET /v1/health` only checks that the process is running.

---

## Configuration

All settings live in `.env` and map to `app/platform/config.py`.

### LLM

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required. OpenRouter API key |
| `OPENROUTER_MODEL` | `google/gemini-2.5-flash-lite` | Default model for all SGR calls |
| `TEACHER_REASONING_MODEL` | `google/gemini-2.5-flash` | Overrides model for teacher turn generation |
| `TEACHER_ANSWER_CHECK_MODEL` | — | Optional override for answer evaluation |
| `TEACHER_SECTION_UNDERSTANDING_MODEL` | — | Optional override for section analysis |

### Embeddings

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_MODEL` | `bge-m3:latest` | Embedding model name |
| `EMBEDDING_BASE_URL` | `http://localhost:11434/v1` | Ollama or OpenRouter-compatible endpoint |
| `EMBEDDING_API_KEY` | — | Required if using OpenRouter embeddings |
| `EMBEDDING_BATCH_SIZE` | `8` | Concurrent embedding requests per batch |

For production with OpenRouter embeddings:
```bash
EMBEDDING_BASE_URL=https://openrouter.ai/api/v1
EMBEDDING_MODEL=baai/bge-m3
EMBEDDING_API_KEY=${OPENROUTER_API_KEY}
```

### Chat & queues

| Variable | Default | Description |
|---|---|---|
| `DURABLE_CHAT_ENABLED` | `true` | Use Celery/RabbitMQ durable path |
| `CHAT_WORKER_QUEUE` | `chat_generation` | RabbitMQ queue name |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672//` | RabbitMQ connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `CHAT_TURN_INLINE_WAIT_SECONDS` | `180.0` | Max wait for a durable turn |

### Auth

| Variable | Default | Description |
|---|---|---|
| `AUTH_SECRET_KEY` | — | Required. Signs session cookies |
| `AUTH_COOKIE_SECURE` | `false` | Set `true` in production (HTTPS) |
| `AUTH_SESSION_TTL_HOURS` | `336` | Session lifetime (14 days) |
| `AUTH_RESET_ENABLED` | `true` | Enable password reset |

### Observability

```bash
LOGFIRE_ENABLED=true
LOGFIRE_TOKEN=...
LOGFIRE_SERVICE_NAME=proactive-its-api
LOGFIRE_ENVIRONMENT=production
```

---

## Data & indexing

Source content (textbook JSON, documents JSONL) lives in `data/` and is tracked by DVC, not Git.

```bash
# Sync data
uv run --extra dev dvc pull

# Check status
uv run --extra dev dvc status
```

**DVC remote:** `s3://proactive-its` on Yandex Cloud (`ru-central1`).

Credentials go in `.dvc/config.local` (never in Git):
```bash
uv run --extra dev dvc remote modify --local production access_key_id <KEY_ID>
uv run --extra dev dvc remote modify --local production secret_access_key <SECRET_KEY>
```

**When to re-index:**
- After `data/` or `data.dvc` changes
- After changing `EMBEDDING_MODEL`
- After changing `CHUNK_TARGET_TOKENS` or `CHUNK_OVERLAP_TOKENS`

Repeated indexing is idempotent — it skips unchanged sections based on a content fingerprint. Use `--force` to override.

```bash
# Normal incremental index
docker compose run --rm indexer

# Force full reindex
docker compose run --rm indexer --force
```

---

## Deployment

### Compose services

| Service | Role |
|---|---|
| `postgres` | Primary database |
| `qdrant` | Vector store |
| `redis` | Pub/sub + embedding cache |
| `rabbitmq` | Task broker |
| `bootstrap` | Runs migrations and seeds default template (one-shot) |
| `indexer` | Indexes content into Qdrant (profile `ops`, run manually) |
| `api` | FastAPI application |
| `worker` | Celery worker (`chat_generation` queue) |
| `web` | Nginx serving built frontend + reverse proxy to API |

### First deploy

```bash
# 1. Clone and configure
cp .env.example .env  # fill required values

# 2. Pull data (requires DVC credentials in .dvc/config.local)
uv sync --extra dev
uv run --extra dev dvc pull

# 3. Start
docker compose up --build -d

# 4. Index content
docker compose run --rm indexer

# 5. Verify
curl http://127.0.0.1/v1/health/ready
```

### Production checklist

- `AUTH_SECRET_KEY` — set to a long random value
- `AUTH_COOKIE_SECURE=true` — HTTPS only
- `AUTH_DEV_LOG_RESET_LINKS=false`
- `CORS_ALLOW_ORIGINS` — set to your actual frontend origin
- `ALLOWED_HOSTS` — set to your actual domain
- SMTP configured if `AUTH_RESET_ENABLED=true`
- Secrets not committed: API keys, DB passwords, `AUTH_SECRET_KEY`, DVC credentials

### Secrets that must never be committed

- `OPENROUTER_API_KEY`
- `POSTGRES_PASSWORD`, `RABBITMQ_DEFAULT_PASS`
- `AUTH_SECRET_KEY`
- `LOGFIRE_TOKEN`
- DVC / Yandex Cloud credentials

# C4 Architecture

## Scope

This document describes the core runtime architecture of the project using the C4 model:

1. System Context
2. Container
3. Component
4. Code

The focus is on the production paths that are visible in the current repository:

- Telegram bot runtime from `main.py`
- Instagram webhook API from `webhooks/instagram_webhook.py`
- Background worker from `workers/comments_worker.py`
- Shared PostgreSQL persistence from `db/`

Utility scripts such as `parsing/*`, `get_one_reel.py`, `random_sample.py` and `ingest_media_to_db.py` are not part of the main C4 boundary below.

## Level 1. System Context

### System under consideration

`try_insta` is a small automation platform for two communication channels:

- Telegram bot conversations with an administrator or operator
- Instagram comment intake, reply generation and optional auto-reply publishing

### External actors and systems

| Actor / System | Role | Interaction with `try_insta` |
| --- | --- | --- |
| Telegram administrator | Operates the bot and can stop polling | Sends messages and commands to the Telegram bot |
| Instagram audience | Leaves comments under Instagram media | Triggers webhook events and receives generated replies |
| Telegram platform | Delivers bot updates | Sends updates to aiogram polling or webhook handler |
| Meta / Instagram Graph API | Source of webhook events and target for comment replies | Calls webhook endpoints and accepts reply publishing requests |
| OpenAI-compatible LLM API | Generates Telegram bot answers and, optionally, Instagram replies | Receives chat completion requests from `LLMAdapter` |
| Dify API / webhook | Optional alternative reply provider and outbound integration | Generates replies or receives mirrored comment events |
| Langfuse | Optional tracing and observability | Receives traces, spans and metadata from runtime flows |

### Context view

```text
[Telegram administrator]
        |
        v
    [try_insta] <---------------------- [Telegram platform]
        |
        +-----------------------------> [OpenAI-compatible LLM API]
        |
        +-----------------------------> [Dify API / webhook]
        |
        +-----------------------------> [Langfuse]
        ^
        |
[Instagram audience] -> [Meta / Instagram Graph API] -> [try_insta]
                                                    ^
                                                    |
                                             reply publication
```

## Level 2. Container

At deployment level the project is split into several runnable containers/processes. The main runtime shape is visible in `docker-compose.yml`.

| Container | Technology | Responsibility | Main dependencies |
| --- | --- | --- | --- |
| `telegram-bot` | Python, aiogram | Handles Telegram polling, commands and user dialogs | Telegram platform, OpenAI-compatible API |
| `webhook` | Python, FastAPI, Uvicorn | Accepts Instagram webhook verification and event delivery, validates signatures, stores events, enqueues comment tasks | Meta webhook delivery, PostgreSQL, Langfuse |
| `worker` | Python background process | Pulls pending comment tasks, generates reply text, optionally publishes replies to Instagram | PostgreSQL, OpenAI-compatible API or Dify, Meta Graph API, Langfuse |
| `postgres` | PostgreSQL | Stores webhook events, comment task queue and bot settings | Shared by `webhook` and `worker` |

### Supporting deployment services

These services are optional from the application point of view, but present in `docker-compose.yml`:

- `langfuse`: observability UI and API
- `langfuse-db`: PostgreSQL for Langfuse itself

### Container view

```text
                            +-----------------------------+
                            |  Telegram platform          |
                            +-------------+---------------+
                                          |
                                          v
+------------------+            +-------------------------+
| Telegram admin   |----------->| telegram-bot            |
+------------------+            | main.py + aiogram       |
                                +-------------+-----------+
                                              |
                                              v
                                     +--------+--------+
                                     | OpenAI-compatible|
                                     | API / Dify API   |
                                     +------------------+

+------------------+            +-------------------------+
| Meta webhook     |----------->| webhook                 |
| delivery         |            | FastAPI / Uvicorn       |
+------------------+            +-------------+-----------+
                                              |
                                              v
                                       +------+------+
                                       | postgres    |
                                       | task queue  |
                                       +------+------+
                                              ^
                                              |
                                +-------------+-----------+
                                | worker                  |
                                | comments_worker.py      |
                                +-------------+-----------+
                                              |
                         +--------------------+--------------------+
                         v                                         v
               +---------+----------+                    +---------+----------+
               | OpenAI-compatible  |                    | Meta Graph API     |
               | API / Dify API     |                    | publish reply      |
               +--------------------+                    +--------------------+

All main runtime containers can also emit traces to Langfuse.
```

## Level 3. Component

Level 3 is shown per runtime container, because that is the most useful way to keep the architecture close to the current codebase.

### 3.1 Webhook container

Implemented mainly in `webhooks/instagram_webhook.py`.

| Component | Code | Responsibility | Depends on |
| --- | --- | --- | --- |
| HTTP entrypoints | `verify_webhook()`, `receive_webhook()` | Handles Meta verification and event delivery | FastAPI request/response layer |
| Startup coordinator | `on_startup()` | Creates tables, configures logging, optionally starts embedded worker thread | `db.ensure_tables()`, `_maybe_start_comments_worker()` |
| Signature validator | `_validate_signature()` | Validates `x-hub-signature-256` with `META_APP_SECRET` | `hashlib`, `hmac` |
| Event persistence gateway | `insert_ig_event()` | Stores raw webhook payload and headers | `db.schema`, PostgreSQL |
| Comment task extractor | `extract_comment_tasks()` | Converts webhook payload into normalized comment tasks | `integrations.instagram.webhook_parse` |
| Queue writer | `enqueue_comment_tasks()` | Writes unique comment tasks into `ig_comment_task` | `db.schema`, PostgreSQL |
| Observability adapter | `build_trace_context()`, `update_observation()`, `propagate_langfuse_attributes()` | Wraps request flow in traces and spans | `integrations.langfuse_support` |
| Optional local worker bootstrap | `_maybe_start_comments_worker()` | Runs `CommentsWorker` in-process when enabled by env | `workers.comments_worker` |

#### Webhook component flow

```text
HTTP request
  -> verify_webhook() or receive_webhook()
  -> _validate_signature() for POST
  -> insert_ig_event()
  -> extract_comment_tasks()
  -> enqueue_comment_tasks()
  -> JSON response
```

### 3.2 Worker container

Implemented mainly in `workers/comments_worker.py`.

| Component | Code | Responsibility | Depends on |
| --- | --- | --- | --- |
| Polling loop | `CommentsWorker.run_forever()` | Continuously polls the task queue and keeps worker alive | `claim_next_comment_task()` |
| Queue repository | `claim_next_comment_task()`, `mark_comment_task_done()`, `mark_comment_task_error()` | Reads and updates task lifecycle in DB | `db.schema`, PostgreSQL |
| Task orchestrator | `CommentsWorker._process_task()` | Applies reply mode rules, handles tracing, sends final status back to DB | Queue repository, reply builders, Graph API |
| Reply provider selector | `_read_reply_provider()`, `_build_reply_service()` | Chooses `chat` or `dify` path | `build_llm_adapter_from_env()`, `build_dify_chat_adapter_from_env()` |
| Prompt builder | `_build_reply_prompt()` | Converts comment payload into a reply generation prompt | Task data |
| Reply generator | `CommentsWorker._build_reply()` | Calls selected provider and normalizes output | `LLMAdapter` or `DifyChatAdapter` |
| Instagram reply gateway | `_send_reply()`, `reply_to_comment()` | Publishes reply comment to Instagram | `InstagramGraphClient` |
| Reply mode resolver | `get_comment_reply_mode()` | Resolves `off`, `draft`, `auto` with DB override | `bot_settings` table |
| Observability adapter | Langfuse helper calls | Tracks each task, generation and publication step | `integrations.langfuse_support` |

#### Worker component flow

```text
run_forever()
  -> claim_next_comment_task()
  -> _process_task()
     -> get_comment_reply_mode()
     -> _build_reply()
        -> LLMAdapter.reply() or DifyChatAdapter.reply()
     -> _send_reply() when mode=auto
     -> mark_comment_task_done() / mark_comment_task_error()
```

### 3.3 Telegram bot container

Implemented mainly in `main.py`, `app_settings.py` and `integrations/telegram_bot/app.py`.

| Component | Code | Responsibility | Depends on |
| --- | --- | --- | --- |
| Configuration loader | `load_app_settings()` | Reads bot and LLM settings from environment | `config.py`, `TelegramAuthConfig`, `LLMConfig` |
| Bot bootstrap | `main.py` | Wires settings, LLM adapter and Telegram app | `build_llm_adapter()`, `TelegramBotApp` |
| Telegram transport | `TelegramBotApp.run_polling()`, `webhook_handler()` | Handles aiogram polling or webhook update feed | Telegram platform |
| Command handlers | `/start`, `/stop` handlers in `_register_handlers()` | Welcomes users and allows admin-controlled shutdown | `is_admin_user()` |
| Conversation handler | `catch_all()` | Passes user text to the configured reply service | `LLMAdapter` |
| Access control | `TelegramAuthConfig`, `is_admin_user()` | Restricts privileged commands | Admin ID from env |

#### Telegram component flow

```text
main.py
  -> load_app_settings()
  -> build_llm_adapter()
  -> TelegramBotApp.run_polling()
  -> catch_all()
  -> reply_service.reply()
  -> message.answer()
```

### 3.4 Shared data components

Implemented in `db/engine.py` and `db/schema.py`.

| Component | Code | Responsibility |
| --- | --- | --- |
| Engine factory | `get_engine()` | Creates one shared SQLAlchemy engine |
| Schema bootstrap | `ensure_tables()` | Creates `ig_event`, `ig_comment_task`, `bot_settings` |
| Event repository | `insert_ig_event()` | Stores raw incoming webhook events |
| Comment task queue | `enqueue_comment_tasks()`, `claim_next_comment_task()` | Provides queue semantics on top of PostgreSQL |
| Task status repository | `mark_comment_task_done()`, `mark_comment_task_error()` | Stores processing results |
| Settings repository | `get_comment_reply_mode()`, `set_comment_reply_mode()` | Keeps runtime reply mode in DB |

### 3.5 Data model

| Table | Purpose |
| --- | --- |
| `ig_event` | Raw inbound Instagram webhook events with headers and signature result |
| `ig_comment_task` | Queue of normalized comment-processing tasks and their outcomes |
| `bot_settings` | Small key-value store for runtime behavior such as `comment_reply_mode` |

## Level 4. Code

C4 level 4 is usually created for a specific container. For this Python project, the most useful code-level view is a map of the main classes, modules and call chains that implement the business flow.

### 4.1 Instagram webhook code path

| Code element | File | Role |
| --- | --- | --- |
| `app` | `webhooks/instagram_webhook.py` | FastAPI application object |
| `verify_webhook()` | `webhooks/instagram_webhook.py` | Meta verification handshake |
| `receive_webhook()` | `webhooks/instagram_webhook.py` | Main POST entrypoint |
| `_validate_signature()` | `webhooks/instagram_webhook.py` | HMAC verification |
| `extract_comment_tasks()` | `integrations/instagram/webhook_parse.py` | Payload normalization |
| `insert_ig_event()` | `db/schema.py` | Event persistence |
| `enqueue_comment_tasks()` | `db/schema.py` | Queue write side |

```text
receive_webhook()
  -> request.body()
  -> _validate_signature()
  -> json.loads()
  -> insert_ig_event(payload, headers, signature_valid)
  -> extract_comment_tasks(payload)
  -> enqueue_comment_tasks(tasks, source_event_id)
  -> JSONResponse(...)
```

### 4.2 Comment processing code path

| Code element | File | Role |
| --- | --- | --- |
| `CommentsWorker` | `workers/comments_worker.py` | Main orchestration class |
| `run_forever()` | `workers/comments_worker.py` | Infinite polling loop |
| `_process_task()` | `workers/comments_worker.py` | Per-task state machine |
| `_build_reply()` | `workers/comments_worker.py` | Reply generation wrapper |
| `_build_reply_prompt()` | `workers/comments_worker.py` | Prompt assembly |
| `_send_reply()` | `workers/comments_worker.py` | Reply publication wrapper |
| `InstagramGraphClient` | `integrations/instagram/graph.py` | HTTP client for Meta Graph API |
| `reply_to_comment()` | `integrations/instagram/comments.py` | Reply POST helper |

```text
CommentsWorker.run_forever()
  -> claim_next_comment_task()
  -> CommentsWorker._process_task(task)
     -> get_comment_reply_mode()
     -> _is_self_authored_comment()
     -> CommentsWorker._build_reply(task)
        -> CommentsWorker._build_reply_prompt(task)
        -> reply_service.reply(...)
     -> CommentsWorker._send_reply(comment_id, reply_text)
        -> reply_to_comment(self.graph, comment_id, reply_text)
     -> mark_comment_task_done(...) or mark_comment_task_error(...)
```

### 4.3 Telegram conversation code path

| Code element | File | Role |
| --- | --- | --- |
| `load_app_settings()` | `app_settings.py` | Aggregates runtime configuration |
| `build_llm_adapter()` | `integrations/ai/factory.py` | Constructs the default reply backend |
| `TelegramBotApp` | `integrations/telegram_bot/app.py` | aiogram application wrapper |
| `_register_handlers()` | `integrations/telegram_bot/app.py` | Registers bot commands and catch-all handler |
| `catch_all()` | `integrations/telegram_bot/app.py` | Main message-to-reply path |
| `LLMAdapter.reply()` | `integrations/ai/adapter.py` | User-facing reply generation |

```text
main.py
  -> load_app_settings(read_env_var, read_env_var_optional)
  -> build_llm_adapter(app_settings.llm)
  -> TelegramBotApp(...).run_polling()
  -> catch_all(message)
  -> asyncio.to_thread(reply_service.reply, user_id, message.text)
  -> LLMAdapter.reply()
  -> message.answer(reply_text)
```

### 4.4 Reply provider code map

| Provider path | Main code | Notes |
| --- | --- | --- |
| Default chat provider | `integrations/ai/adapter.py` | OpenAI-compatible HTTP client with history, rate limit and friendly error mapping |
| Provider factory | `integrations/ai/factory.py` | Builds `LLMAdapter` from config or env |
| Alternative provider | `integrations/dify/adapter.py` | Dify chat integration and outbound webhook mirroring |

### 4.5 Observability code map

| Code element | File | Role |
| --- | --- | --- |
| `get_langfuse_client()` | `integrations/langfuse_support.py` | Lazy Langfuse client factory |
| `build_trace_context()` | `integrations/langfuse_support.py` | Stable trace IDs per event or comment |
| `propagate_langfuse_attributes()` | `integrations/langfuse_support.py` | Trace enrichment |
| `update_observation()` | `integrations/langfuse_support.py` | Safe span/observation updates |
| `serialize_for_langfuse()` | `integrations/langfuse_support.py` | Metadata normalization |

## Architectural notes

- The project uses PostgreSQL not only as durable storage, but also as a lightweight work queue.
- `ig_comment_task` is intentionally the integration seam between the webhook API and the worker.
- Reply mode is dynamic: `bot_settings.comment_reply_mode` overrides the environment default and lets the system switch between `off`, `draft` and `auto`.
- Langfuse is cross-cutting and touches webhook, worker and provider code, but it does not own business logic.
- The project has two distinct user-facing channels, Telegram and Instagram, that share some infrastructure but have different runtime entrypoints.

# CollabSpace

**A real-time collaborative workspace — Kanban boards, live documents, and a shared whiteboard your whole team edits together, with presence, chat, and an activity feed.**

[![CI](https://github.com/Kushe602/Real-Time-Collaborative-App/actions/workflows/ci.yml/badge.svg)](https://github.com/Kushe602/Real-Time-Collaborative-App/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

CollabSpace is a single FastAPI application where a team creates a workspace and
collaborates across three live surfaces at once. Everything is real-time: cards
move, documents type themselves out, sticky notes slide around, and teammates'
cursors and avatars appear as they work — all multiplexed over **one WebSocket
per user per workspace**. It runs with **zero configuration** (SQLite, no API
keys) and scales to Postgres via Docker Compose for a production-shaped setup.

## Features

- **Kanban boards** — lists and cards with live drag-and-drop reordering across
  columns (fractional positioning, so two people reordering never clobber each
  other's indices).
- **Collaborative docs** — a Markdown editor with a live-rendered preview;
  edits stream to everyone with a server-owned version counter (last-write-wins).
- **Shared whiteboard** — sticky notes and shapes you drag around an infinite
  canvas, with editable note text and live remote cursors.
- **Team chat** — per-workspace chat delivered instantly over the same socket.
- **Presence** — a live roster, join/leave events, per-surface cursors, and an
  "editing" pulse on a teammate's avatar.
- **Activity feed** — a running log of what happened in the workspace.

## Tech stack

| Layer | Choice |
| --- | --- |
| Web framework | FastAPI (async) + Uvicorn |
| Realtime | Native WebSockets, one connection per user per workspace |
| Persistence | SQLAlchemy 2.0 async ORM — SQLite (default) / Postgres (asyncpg) |
| Templating | Jinja2 server-rendered partials |
| Frontend | Tailwind (CDN) + vanilla JS + SortableJS; no build step |
| Auth | bcrypt password hashing + JWT (HS256) in an httpOnly cookie |

## Architecture

**One socket, many channels.** Each browser opens a single WebSocket to
`/ws/workspace/{id}`. Every message — no matter the surface — shares one
envelope:

```json
{ "channel": "presence|chat|board|doc|whiteboard|system", "type": "...", "...": "payload" }
```

A thin transport loop (`app/realtime/socket.py`) authenticates the cookie,
confirms workspace membership, registers the socket with an in-memory
`ConnectionManager`, and forwards each frame to a per-channel handler
(`app/realtime/handlers.py`). Handlers persist the change and broadcast the
result to the room.

**Reads over HTTP, writes over WebSocket.** Opening a surface fetches a rendered
HTML partial over HTTP (`/workspaces/{id}/board/{board_id}`, etc.). From then on
every *mutation* travels over the socket. This keeps initial paint simple and
cacheable while making collaboration live.

**Broadcast semantics** are deliberate per message type:

| Message | Broadcast to | Why |
| --- | --- | --- |
| `card.create`, `list.create`, `element.create`, chat | **everyone incl. sender** | sender renders on echo, so all clients converge on server-assigned ids |
| `card.move`, `doc.update`, `element.move`, `element.update`, cursors | **everyone except sender** | the sender already applied it optimistically |

**Data model** (`app/models.py`) is intentionally free of ORM `relationship()`
lazy-loading — every association is an explicit query, which plays well with
async sessions. Board ordering uses a fractional `Float` position so a card can
always be inserted between two neighbours without renumbering.

## Quickstart

### Local (zero config, SQLite)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open http://localhost:8000, register two accounts in two browsers, create a
workspace, share the invite code, and watch edits sync live.

### Docker (Postgres)

```bash
docker compose up --build
```

This starts Postgres and the app wired to it; open http://localhost:8000.

## Configuration

Settings load from the environment or a `.env` file (see `.env.example`). All
have sensible defaults so it runs out of the box.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | dev placeholder | signs JWT session cookies — **set a real one in production** |
| `DATABASE_URL` | `sqlite+aiosqlite:///./collabspace.db` | SQLAlchemy async URL; Compose overrides with asyncpg/Postgres |
| `WS_HEARTBEAT_SECONDS` | `25` | idle interval before the server pings a socket |

## Tests

```bash
pytest -q          # HTTP flows via Starlette TestClient + full WebSocket tests
ruff check .       # lint
```

The suite covers auth, workspace membership gating, the surface partials, and
the realtime layer end-to-end (presence roster, chat echo, and live board / doc
/ whiteboard mutations verified through to server-side persistence).

## Security notes

- **Auth**: passwords are bcrypt-hashed; sessions are a JWT (HS256) in an
  **httpOnly**, `SameSite=Lax` cookie. Every HTTP surface route and the
  WebSocket verify **workspace membership** before returning or mutating data,
  and unknown/forbidden resources return `404` so existence isn't leaked.
- **Cookie over HTTP in dev**: the session cookie is sent with `secure=False`
  so it works over plain `http://localhost`. **Set `secure=True` behind HTTPS in
  production** (see `_set_session_cookie` in `app/routers/auth.py`).
- **Invite code == workspace id**: joining a workspace uses its raw id as the
  invite code. It's an unguessable 128-bit value, but anyone with the id can
  join — treat it like a shared secret. Add per-invite tokens/expiry for a
  stricter model.
- **Untrusted input**: doc Markdown is escaped before the client introduces its
  own tags (no raw HTML injection), and whiteboard element data is whitelisted
  and coerced server-side (`_clean_element_data`) with length caps.

## Project layout

```
app/
  main.py            # app wiring: lifespan, routers, static, auth error handler
  config.py          # pydantic-settings
  database.py        # async engine + session + Base + init_db
  models.py          # users, workspaces, boards, docs, whiteboards, chat, activity
  security.py        # bcrypt + JWT + cookie name
  services.py        # membership checks, activity log, member queries
  dependencies.py    # current-user resolution from the cookie
  routers/           # auth, workspaces, surfaces (HTTP)
  realtime/          # socket (transport), handlers (channels), manager (presence)
  templates/         # Jinja2 shell + per-surface partials
  static/            # app.css + collab/board/doc/whiteboard JS
tests/               # pytest suite (HTTP + WebSocket)
```

## License

MIT — see [LICENSE](LICENSE).


# Simple State Engine
A lightweight, schema-enforced, in-memory data store for Python.  
Think of it as a tiny cross-platform Redis with tables, CRUD operations, and optional ZMQ transport —  
all in a single script and with zero external infrastructure.

Perfect for:

- real-time internal tools  
- ETL pipelines  
- dashboards  
- worker coordination  
- lead/task queues  
- stateful scripts and automations  
- quick data stores without a database  

Simple State Engine is designed to be embeddable, predictable, and safe.  
Your entire state lives in memory, enforced by schema, versioned, and optionally persisted to disk or a database.

---

## Features

### Schema inference + enforcement
The first record sets the schema. All future writes are validated.

### Full state replacement (SET_STATE)
Replace the entire table atomically with schema validation.

### Record-level CRUD
Supports:
- `CREATE_RECORD`
- `SET_RECORD`
- `PATCH_RECORD`
- `DELETE_RECORD`
- `GET_RECORD`
- `LIST_RECORDS`

### ID handling
Auto-increment IDs or explicit IDs — immutable once assigned.

### Concurrency-safe
Internal async locks keep operations consistent.

### Transport-agnostic
Can run fully in-process, or behind a ZMQ / FastAPI / HTTP wrapper.

### Max Length Controls
States can define an optional `max_length`:
- `SET_STATE` truncates input to the limit  
- `CREATE_RECORD` rejects if at capacity  

### Pluggable persistence
Drop in your own persistence backend (SQLite, Postgres, JSON, etc).

### Pure Python, zero setup
No Redis server, no Docker, no external binaries.

---

# How It Works

The engine manages **multiple named states**, each functioning like a small in-memory table.

Each state:

- Stores records in a dict: `{id: record}`  
- Infers schema from the first record  
- Enforces consistent fields  
- Supports atomic full-state replacement  
- Increments a version counter on each write  
- Can be bounded by `max_length`  

A ZeroMQ daemon is included to expose the engine remotely using simple JSON commands.

---

# State Concepts

## Schema

When the first record enters a state, its fields (plus `"id"`) define the schema.

Example:
```json
["id", "name", "status", "priority"]


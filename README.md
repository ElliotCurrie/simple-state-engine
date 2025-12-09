# Simple State Engine

### Command Reference

| Command          | Description | Required Fields | Notes |
|------------------|-------------|------------------|-------|
| `CREATE_STATE`   | Creates a new named state. | `state`, optional `max_length` | State starts empty. Throws error if already exists. |
| `DELETE_STATE`   | Deletes an existing state. | `state` | State and all records are removed. |
| `LIST_STATES`    | Returns all state names. | *(none)* | Useful for dashboards or debugging. |
| `GET_STATE_INFO` | Returns schema, version, size, max_length. | `state` | Does not include actual records. |
| `GET_SCHEMA`     | Returns inferred schema for a state. | `state` | Schema is inferred after first write. |
| `SET_STATE`      | Atomically replaces entire state. | `state`, `data` (list) | Truncates to `max_length` if necessary. Defines schema if none exists. |
| `CREATE_RECORD`  | Inserts a new record. | `state`, `data` (dict) | Auto-assigns ID if missing. Rejects if at `max_length`. |
| `SET_RECORD`     | Replaces a record entirely. | `state`, `id`, `data` | Must match schema exactly (including `id`). |
| `PATCH_RECORD`   | Partially updates a record. | `state`, `id`, `data` | Cannot introduce unknown fields. |
| `DELETE_RECORD`  | Removes a record. | `state`, `id` | No error if record does not exist. |
| `GET_RECORD`     | Fetches one record. | `state`, `id` | Returns null if missing. |
| `LIST_RECORDS`   | Returns all records. | `state` | Locked during copy to avoid inconsistency. |

---

## Description

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
```

# Tutorial

This tutorial walks through how to use the Simple State Engine end-to-end:

1. Start the daemon  
2. Connect using a client  
3. Create a state  
4. Load data  
5. Mutate records  
6. Enforce schema rules  
7. Work with `max_length`  
8. Query metadata  
9. Persist data (optional)

---

## 1. Start the State Engine Daemon

Run the daemon:

```bash
python shared_state.py
```

Expected output:

```
[STATE DAEMON] Running at tcp://127.0.0.1:5560
```

---

## 2. Connect With a Python Client

```python
import zmq

ctx = zmq.Context()
sock = ctx.socket(zmq.REQ)
sock.connect("tcp://127.0.0.1:5560")

def call(payload):
    sock.send_json(payload)
    return sock.recv_json()
```

---

## 3. Create a State

```python
call({
    "cmd": "CREATE_STATE",
    "state": "leads",
    "max_length": 1000
})
```

---

## 4. Load Data With `SET_STATE`

```python
call({
    "cmd": "SET_STATE",
    "state": "leads",
    "data": [
        {"name": "Alice", "status": "open"},
        {"name": "Bob",   "status": "open"}
    ]
})
```

Example response:

```json
{ "status": "ok", "accepted": 2, "dropped": 0 }
```

Schema inferred:

```
["id", "name", "status"]
```

---

## 5. Create a Record

```python
call({
    "cmd": "CREATE_RECORD",
    "state": "leads",
    "data": {"name": "Charlie", "status": "open"}
})
```

Response:

```json
{ "status": "ok", "id": 3 }
```

---

## 6. Retrieve a Record

```python
call({
    "cmd": "GET_RECORD",
    "state": "leads",
    "id": 3
})
```

---

## 7. Update a Record (`PATCH_RECORD`)

```python
call({
    "cmd": "PATCH_RECORD",
    "state": "leads",
    "id": 3,
    "data": {"status": "closed"}
})
```

Invalid example:

```json
{
  "status": "error",
  "message": "PATCH contains unknown fields: {'priority'}"
}
```

---

## 8. Replace a Record (`SET_RECORD`)

```python
call({
    "cmd": "SET_RECORD",
    "state": "leads",
    "id": 2,
    "data": {"id": 2, "name": "Bob", "status": "archived"}
})
```

---

## 9. List All Records

```python
call({
    "cmd": "LIST_RECORDS",
    "state": "leads"
})
```

---

## 10. Delete a Record

```python
call({
    "cmd": "DELETE_RECORD",
    "state": "leads",
    "id": 1
})
```

---

## 11. Use `max_length` to Control State Size

### Create a capped state

```python
call({
    "cmd": "CREATE_STATE",
    "state": "campaign",
    "max_length": 1500
})
```

### `SET_STATE` truncates oversized data

```python
call({
    "cmd": "SET_STATE",
    "state": "campaign",
    "data": big_list
})
```

Example response:

```json
{ "status": "ok", "accepted": 1500, "dropped": 132 }
```

### `CREATE_RECORD` fails when full

```json
{
  "status": "error",
  "message": "State 'campaign' has reached max_length (1500)"
}
```

---

## 12. Inspect State Metadata

```python
call({
    "cmd": "GET_STATE_INFO",
    "state": "leads"
})
```

Example response:

```json
{
  "status": "ok",
  "name": "leads",
  "max_length": 1000,
  "current_length": 3,
  "schema": ["id", "name", "status"],
  "version": 7
}
```

---

## 13. Optional: Persistence

Define a persistence callback:

```python
async def persist_to_db(state_name, delta):
    # Write to Postgres, SQLite, JSON, etc.
    ...
```

---

# Summary

This tutorial covered:

- Running the daemon  
- Creating states  
- Loading and replacing data  
- CRUD operations  
- Schema enforcement  
- Using bounded states with `max_length`  
- Retrieving metadata and version numbers  
- Optional persistence

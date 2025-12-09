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

`Simple State Engine` is designed to be embeddable, predictable, and safe.  
Your entire state lives in memory, enforced by schema, versioned, and optionally persisted to disk or a database.

---

## Features

- **Schema inference + enforcement**  
  The first record sets the schema. All future writes are validated.

- **Full state replacement (`SET_STATE`)**  
  Replace the entire table atomically with schema validation.

- **Record-level CRUD**  
  `CREATE_RECORD`, `SET_RECORD`, `PATCH_RECORD`, `DELETE_RECORD`, `GET_RECORD`, `LIST_RECORDS`.

- **ID handling**  
  Auto-increment IDs or explicit IDs — immutable once assigned.

- **Concurrency-safe**  
  Internal async locks keep operations consistent.

- **Transport-agnostic**  
  Can run fully in-process, or behind a ZMQ / FastAPI / HTTP wrapper.

- **Pluggable persistence**  
  Drop in your own persistence backend (SQLite, Postgres, JSON, etc).

- **Pure Python, zero setup**  
  No Redis server, no Docker, no external binaries.

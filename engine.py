import asyncio
import json
import sys
from typing import Dict, Any, List
import zmq.asyncio


# Use selector event loop on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ============================================================
# SharedState:
#   - Maintains an in-memory table of records
#   - Enforces a fixed schema
#   - Supports full-state replacement, record CRUD,
#     optional max_length truncation, and versioning
# ============================================================
class SharedState:
    def __init__(self, name: str, max_length: int | None = None):
        self.name = name
        self.max_length = max_length
        self.records: Dict[int, Dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self.version = 0
        self.schema = None
        self.persist = None  # Async persistence callback

    # ------------------------------
    # Schema management
    # ------------------------------
    def _infer_schema(self, record: dict):
        self.schema = set(record.keys()) | {"id"}

    def _validate_full_schema(self, record: dict):
        incoming = set(record.keys())
        if incoming != self.schema:
            missing = self.schema - incoming
            extra = incoming - self.schema
            raise ValueError(f"Schema mismatch. Missing={missing}, Extra={extra}")

    def _validate_patch_schema(self, changes: dict):
        illegal = set(changes.keys()) - self.schema
        if illegal:
            raise ValueError(f"PATCH contains unknown fields: {illegal}")

    # ------------------------------
    # Replace entire state
    # ------------------------------
    async def set_state(self, records: List[dict]):
        async with self.lock:
            if not records:
                self.records.clear()
                self.version += 1
                return {"accepted": 0, "dropped": 0}

            dropped = 0

            # Apply max_length truncation if enabled
            if self.max_length is not None and len(records) > self.max_length:
                dropped = len(records) - self.max_length
                records = records[:self.max_length]

            normalized = []
            seen_ids = set()

            # Schema inference or validation
            first = dict(records[0])
            first_fields = set(first.keys()) - {"id"}

            if self.schema is None:
                self.schema = first_fields | {"id"}
            else:
                expected = self.schema - {"id"}
                if first_fields != expected:
                    missing = expected - first_fields
                    extra = first_fields - expected
                    raise ValueError(
                        f"SET_STATE schema mismatch. Missing={missing}, Extra={extra}"
                    )

            # Normalization + ID assignment
            next_id = 1
            for rec in records:
                rec = dict(rec)
                rid = rec.get("id")

                if rid is None:
                    # Auto-assign sequential IDs
                    while next_id in seen_ids:
                        next_id += 1
                    rid = next_id
                    rec["id"] = rid
                    seen_ids.add(rid)
                else:
                    rid = int(rid)
                    if rid in seen_ids:
                        raise ValueError(f"Duplicate id {rid} in SET_STATE")
                    seen_ids.add(rid)

                self._validate_full_schema(rec)
                normalized.append(rec)

            # Commit new state
            self.records = {rec["id"]: rec for rec in normalized}
            self.version += 1

            if self.persist:
                await self.persist(self.name, {
                    "set_state": len(normalized),
                    "dropped": dropped
                })

            return {"accepted": len(normalized), "dropped": dropped}

    # ------------------------------
    # Create a single record
    # ------------------------------
    async def create_record(self, record: dict) -> int:
        async with self.lock:
            if self.max_length is not None and len(self.records) >= self.max_length:
                raise ValueError(
                    f"State '{self.name}' has reached max_length ({self.max_length})"
                )

            rec = dict(record)

            # Schema inference or validation
            if self.schema is None:
                self._infer_schema(rec)
            else:
                incoming = set(rec.keys()) | {"id"} if "id" not in rec else set(rec.keys())
                if incoming != self.schema:
                    missing = self.schema - incoming
                    extra = incoming - self.schema
                    raise ValueError(
                        f"CREATE_RECORD schema mismatch. Missing={missing}, Extra={extra}"
                    )

            # Assign ID if missing
            rid = rec.get("id")
            if rid is None:
                rid = max(self.records.keys(), default=0) + 1
                rec["id"] = rid
            else:
                rid = int(rid)
                if rid in self.records:
                    raise ValueError(f"Record id {rid} already exists")

            self.records[rid] = rec
            self.version += 1

            if self.persist:
                await self.persist(self.name, {"create_record": rec})

            return rid

    # ------------------------------
    # Replace a single record
    # ------------------------------
    async def set_record(self, record_id: int, record: dict):
        async with self.lock:
            rec = dict(record)
            rec["id"] = record_id

            if self.schema is None:
                self._infer_schema(rec)
            else:
                self._validate_full_schema(rec)

            self.records[record_id] = rec
            self.version += 1

            if self.persist:
                await self.persist(self.name, {"set_record": rec})

            return True

    # ------------------------------
    # Patch a record
    # ------------------------------
    async def patch_record(self, record_id: int, changes: dict):
        rec = self.records.get(record_id)
        if not rec:
            return False

        if self.schema is None:
            raise ValueError("Cannot PATCH before schema is defined")

        changes = dict(changes)
        changes.pop("id", None)

        self._validate_patch_schema(changes)

        rec.update(changes)
        self.version += 1

        if self.persist:
            await self.persist(self.name, {
                "patch_record": {"id": record_id, **changes}
            })

        return True

    # ------------------------------
    # Delete a record
    # ------------------------------
    async def delete_record(self, record_id: int):
        async with self.lock:
            if record_id in self.records:
                del self.records[record_id]
                self.version += 1

                if self.persist:
                    await self.persist(self.name, {"delete_record": record_id})

                return True
            return False

    # ------------------------------
    # Read operations
    # ------------------------------
    async def get_record(self, record_id: int):
        return self.records.get(record_id)

    async def list_records(self):
        async with self.lock:
            return list(self.records.values())

    def get_schema(self):
        return sorted(self.schema) if self.schema else None

    def get_state_info(self):
        return {
            "name": self.name,
            "max_length": self.max_length,
            "current_length": len(self.records),
            "schema": self.get_schema(),
            "version": self.version,
        }


# ============================================================
# Registry for multiple states
# ============================================================
class StateRegistry:
    def __init__(self):
        self.states: Dict[str, SharedState] = {}

    def create(self, name: str, max_length: int | None = None):
        if name in self.states:
            raise ValueError(f"State '{name}' already exists")
        st = SharedState(name, max_length=max_length)
        st.persist = persist_to_db
        self.states[name] = st
        return st

    def get(self, name: str) -> SharedState:
        if name not in self.states:
            raise ValueError(f"State '{name}' not found")
        return self.states[name]

    def delete(self, name: str):
        if name not in self.states:
            raise ValueError(f"State '{name}' not found")
        del self.states[name]

    def list(self):
        return list(self.states.keys())


registry = StateRegistry()


# ============================================================
# Persistence hook (user-implemented)
# ============================================================
async def persist_to_db(state_name: str, delta: dict):
    print(f"[PERSIST] state={state_name} delta={delta}")


# ============================================================
# ZMQ command router
# ============================================================
async def handle_message(msg: dict) -> dict:
    cmd = (msg.get("cmd") or "").upper()

    # State management
    if cmd == "CREATE_STATE":
        st = registry.create(msg["state"], msg.get("max_length"))
        return {"status": "ok", "created": msg["state"], "max_length": st.max_length}

    if cmd == "DELETE_STATE":
        registry.delete(msg["state"])
        return {"status": "ok"}

    if cmd == "LIST_STATES":
        return {"status": "ok", "states": registry.list()}

    if cmd == "GET_STATE_INFO":
        st = registry.get(msg["state"])
        return {"status": "ok", **st.get_state_info()}

    if cmd == "GET_SCHEMA":
        st = registry.get(msg["state"])
        return {"status": "ok", "schema": st.get_schema()}

    # Remaining commands require an existing state
    if "state" not in msg:
        return {"status": "error", "message": "Missing 'state' field"}

    try:
        st = registry.get(msg["state"])
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # State operations
    if cmd == "SET_STATE":
        info = await st.set_state(msg.get("data") or [])
        return {"status": "ok", **info}

    if cmd == "CREATE_RECORD":
        new_id = await st.create_record(msg["data"])
        return {"status": "ok", "id": new_id}

    if cmd == "SET_RECORD":
        rid = int(msg["id"])
        await st.set_record(rid, msg["data"])
        return {"status": "ok"}

    if cmd == "PATCH_RECORD":
        rid = int(msg["id"])
        ok = await st.patch_record(rid, msg["data"])
        return {"status": "ok", "patched": ok}

    if cmd == "DELETE_RECORD":
        rid = int(msg["id"])
        ok = await st.delete_record(rid)
        return {"status": "ok", "deleted": ok}

    if cmd == "GET_RECORD":
        rid = int(msg["id"])
        rec = await st.get_record(rid)
        return {"status": "ok", "record": rec}

    if cmd == "LIST_RECORDS":
        recs = await st.list_records()
        return {"status": "ok", "records": recs}

    return {"status": "error", "message": f"Unknown command '{cmd}'"}


# ============================================================
# Main daemon loop
# ============================================================
async def main():
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind("tcp://127.0.0.1:5560")

    print("[STATE DAEMON] Running at tcp://127.0.0.1:5560")

    while True:
        raw = await sock.recv()
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            await sock.send_json({"status": "error", "message": "invalid JSON"})
            continue

        try:
            resp = await handle_message(msg)
        except ValueError as ve:
            resp = {"status": "error", "message": str(ve)}
        except Exception as e:
            resp = {"status": "error", "message": f"server error: {e}"}

        await sock.send_json(resp)


if __name__ == "__main__":
    asyncio.run(main())

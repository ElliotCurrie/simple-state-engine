import asyncio
import json
import sys
from typing import Dict, Any, List
import zmq.asyncio


# ============================================================
# Windows loop fix
# ============================================================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ============================================================
# SharedState with schema enforcement + new naming
# ============================================================
class SharedState:
    def __init__(self, name: str):
        self.name = name
        self.records: Dict[int, Dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self.version = 0
        self.schema = None        # set of field names including "id"
        self.persist = None       # async callback: await persist(state_name, delta)

    # ----------------------------------------------------------
    # Schema helpers
    # ----------------------------------------------------------
    def _infer_schema(self, record: dict):
        """Set schema based on first record."""
        self.schema = set(record.keys()) | {"id"}

    def _validate_full_schema(self, record: dict):
        """Ensure record matches schema exactly."""
        incoming = set(record.keys())
        if incoming != self.schema:
            missing = self.schema - incoming
            extra = incoming - self.schema
            raise ValueError(f"Schema mismatch. Missing={missing}, Extra={extra}")

    def _validate_patch_schema(self, changes: dict):
        """Ensure PATCH does not introduce new fields."""
        illegal = set(changes.keys()) - self.schema
        if illegal:
            raise ValueError(f"PATCH attempted unknown fields: {illegal}")

    # ----------------------------------------------------------
    # SET_STATE (replace whole state)
    # ----------------------------------------------------------
    async def set_state(self, records: List[dict]):
        async with self.lock:
            if not records:
                self.records.clear()
                self.version += 1
                return

            normalized = []
            seen_ids = set()

            # Determine schema from first record (ignoring id)
            first = dict(records[0])
            first_fields = set(first.keys()) - {"id"}

            if self.schema is None:
                self.schema = first_fields | {"id"}
            else:
                expected = self.schema - {"id"}
                if first_fields != expected:
                    missing = expected - first_fields
                    extra = first_fields - expected
                    raise ValueError(f"SET_STATE schema mismatch. Missing={missing}, Extra={extra}")

            # Assign IDs and validate fields
            next_id = 1

            for rec in records:
                rec = dict(rec)

                # assign ID if missing
                rid = rec.get("id")
                if rid is None:
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

                # validate schema
                self._validate_full_schema(rec)
                normalized.append(rec)

            # replace state
            self.records = {rec["id"]: rec for rec in normalized}
            self.version += 1

            if self.persist:
                await self.persist(self.name, {"set_state": len(normalized)})

    # ----------------------------------------------------------
    # CREATE_RECORD
    # ----------------------------------------------------------
    async def create_record(self, record: dict) -> int:
        async with self.lock:
            rec = dict(record)

            # First insert sets schema
            if self.schema is None:
                self._infer_schema(rec)
            else:
                incoming = set(rec.keys()) | {"id"} if "id" not in rec else set(rec.keys())
                missing = self.schema - incoming
                extra = incoming - self.schema
                if missing or extra:
                    raise ValueError(
                        f"CREATE_RECORD schema mismatch. Missing={missing}, Extra={extra}"
                    )

            # auto-assign ID if missing
            rid = rec.get("id")
            if rid is None:
                rid = max(self.records.keys(), default=0) + 1
                rec["id"] = rid
            else:
                rid = int(rid)
                if rid in self.records:
                    raise ValueError(f"Record id {rid} already exists")

            # insert
            self.records[rid] = rec
            self.version += 1

            if self.persist:
                await self.persist(self.name, {"create_record": rec})

            return rid

    # ----------------------------------------------------------
    # SET_RECORD (full replace)
    # ----------------------------------------------------------
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

    # ----------------------------------------------------------
    # PATCH_RECORD
    # ----------------------------------------------------------
    async def patch_record(self, record_id: int, changes: dict):
        rec = self.records.get(record_id)
        if not rec:
            return False

        if self.schema is None:
            raise ValueError("Cannot PATCH before schema is defined")

        changes = dict(changes)
        changes.pop("id", None)  # id is immutable

        self._validate_patch_schema(changes)

        rec.update(changes)
        self.version += 1

        if self.persist:
            await self.persist(self.name, {"patch_record": {"id": record_id, **changes}})

        return True

    # ----------------------------------------------------------
    # DELETE_RECORD
    # ----------------------------------------------------------
    async def delete_record(self, record_id: int):
        async with self.lock:
            if record_id in self.records:
                del self.records[record_id]
                self.version += 1

                if self.persist:
                    await self.persist(self.name, {"delete_record": record_id})

                return True
            return False

    # ----------------------------------------------------------
    # GET / LIST
    # ----------------------------------------------------------
    async def get_record(self, record_id: int):
        return self.records.get(record_id)

    async def list_records(self):
        async with self.lock:
            return list(self.records.values())

    def get_schema(self):
        return sorted(self.schema) if self.schema else None


# ============================================================
# Registry
# ============================================================
class StateRegistry:
    def __init__(self):
        self.states: Dict[str, SharedState] = {}

    def create(self, name: str):
        if name in self.states:
            raise ValueError(f"State '{name}' already exists")
        st = SharedState(name)
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
# Persistence hook (fill this in later with DB logic)
# ============================================================
async def persist_to_db(state_name: str, delta: dict):
    print(f"[PERSIST] state={state_name} delta={delta}")


# ============================================================
# ZMQ router
# ============================================================
async def handle_message(msg: dict) -> dict:
    cmd = (msg.get("cmd") or "").upper()

    # --- State operations ---
    if cmd == "CREATE_STATE":
        st = registry.create(msg["state"])
        return {"status": "ok", "created": msg["state"]}

    if cmd == "DELETE_STATE":
        registry.delete(msg["state"])
        return {"status": "ok"}

    if cmd == "LIST_STATES":
        return {"status": "ok", "states": registry.list()}

    if cmd == "GET_SCHEMA":
        st = registry.get(msg["state"])
        return {"status": "ok", "schema": st.get_schema()}

    # --- Require a state below this point ---
    if "state" not in msg:
        return {"status": "error", "message": "Missing 'state' field"}

    try:
        st = registry.get(msg["state"])
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # --- SET_STATE ---
    if cmd == "SET_STATE":
        data = msg.get("data") or []
        await st.set_state(data)
        return {"status": "ok", "count": len(data)}

    # --- CREATE_RECORD ---
    if cmd == "CREATE_RECORD":
        new_id = await st.create_record(msg["data"])
        return {"status": "ok", "id": new_id}

    # --- SET_RECORD ---
    if cmd == "SET_RECORD":
        rid = int(msg["id"])
        await st.set_record(rid, msg["data"])
        return {"status": "ok"}

    # --- PATCH_RECORD ---
    if cmd == "PATCH_RECORD":
        rid = int(msg["id"])
        ok = await st.patch_record(rid, msg["data"])
        return {"status": "ok", "patched": ok}

    # --- DELETE_RECORD ---
    if cmd == "DELETE_RECORD":
        rid = int(msg["id"])
        ok = await st.delete_record(rid)
        return {"status": "ok", "deleted": ok}

    # --- GET_RECORD ---
    if cmd == "GET_RECORD":
        rid = int(msg["id"])
        rec = await st.get_record(rid)
        return {"status": "ok", "record": rec}

    # --- LIST_RECORDS ---
    if cmd == "LIST_RECORDS":
        recs = await st.list_records()
        return {"status": "ok", "records": recs}

    return {"status": "error", "message": f"Unknown command '{cmd}'"}


# ============================================================
# Main loop
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

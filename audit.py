"""Append-only audit log for all device commands.

Every command executed against a device is recorded here as one JSONL line with:
session_id, device target, command, exit_status, source, and timestamp.

The log is append-only by design (we never rewrite or delete lines). It powers
the /api/audit endpoint so operators can answer "who ran what against which
device, and what happened" -- a baseline requirement for any production-grade
remote-execution tool.
"""
import os
import json
from datetime import datetime
from typing import Optional

AUDIT_DIR = "data/audit"
AUDIT_FILE = os.path.join(AUDIT_DIR, "audit.jsonl")


def record(
    session_id: Optional[str],
    device: str,
    command: str,
    exit_status: Optional[int] = None,
    source: str = "agent",
    detail: Optional[str] = None,
):
    """Append one audit record. Never raises -- auditing must not break runs."""
    try:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "device": device,
            "command": command,
            "exit_status": exit_status,
            "source": source,
        }
        if detail:
            rec["detail"] = detail[:500]
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[audit] failed to record: {e}")


def query(
    session_id: Optional[str] = None,
    device: Optional[str] = None,
    limit: int = 200,
    source: Optional[str] = None,
):
    """Return recent audit records, newest first, optionally filtered."""
    if not os.path.exists(AUDIT_FILE):
        return []
    out = []
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if session_id and rec.get("session_id") != session_id:
                    continue
                if device and rec.get("device") != device:
                    continue
                if source and rec.get("source") != source:
                    continue
                out.append(rec)
    except Exception as e:
        print(f"[audit] failed to query: {e}")
    return list(reversed(out))[:limit]
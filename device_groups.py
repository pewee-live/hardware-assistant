"""Device group management for batch orchestration.

A device group is a named, ordered list of device targets. Each target stores
enough info to establish a connection independently of any session: host,
conn_type, username, port. Secrets are NOT stored here -- they are resolved from
the encrypted vault at execution time.

This lets the agent say "upgrade kernel on the edge-nodes group" and have one
tool call fan out across N devices concurrently.
"""
import os
import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

GROUPS_DIR = os.path.join("data", "device_groups")


class DeviceTarget:
    """One device in a group."""
    def __init__(self, host: str, conn_type: str = "ssh",
                 username: Optional[str] = None, port: int = 22):
        self.host = host
        self.conn_type = conn_type
        self.username = username
        self.port = port

    def to_dict(self) -> dict:
        return {"host": self.host, "conn_type": self.conn_type,
                "username": self.username, "port": self.port}

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceTarget":
        return cls(
            host=d.get("host", ""),
            conn_type=d.get("conn_type", "ssh"),
            username=d.get("username"),
            port=d.get("port", 22),
        )


class DeviceGroup:
    """A named group of devices."""
    def __init__(self, group_id: str, name: str, targets: List[DeviceTarget],
                 created_at: str = None, updated_at: str = None):
        self.group_id = group_id
        self.name = name
        self.targets = targets
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "targets": [t.to_dict() for t in self.targets],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceGroup":
        return cls(
            group_id=d["group_id"],
            name=d.get("name", "Unnamed Group"),
            targets=[DeviceTarget.from_dict(t) for t in d.get("targets", [])],
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


class DeviceGroupManager:
    """CRUD for device groups, persisted as JSON files under data/device_groups/."""

    def __init__(self):
        os.makedirs(GROUPS_DIR, exist_ok=True)

    def _path(self, group_id: str) -> str:
        return os.path.join(GROUPS_DIR, f"{group_id}.json")

    def create(self, name: str, targets: List[dict]) -> DeviceGroup:
        group_id = uuid.uuid4().hex[:12]
        group = DeviceGroup(
            group_id=group_id,
            name=name or "Unnamed Group",
            targets=[DeviceTarget.from_dict(t) for t in targets],
        )
        self.save(group)
        return group

    def save(self, group: DeviceGroup):
        group.updated_at = datetime.now().isoformat()
        with open(self._path(group.group_id), "w", encoding="utf-8") as f:
            json.dump(group.to_dict(), f, ensure_ascii=False, indent=2)

    def get(self, group_id: str) -> Optional[DeviceGroup]:
        path = self._path(group_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return DeviceGroup.from_dict(json.load(f))

    def list(self) -> List[dict]:
        out = []
        for fn in os.listdir(GROUPS_DIR):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(GROUPS_DIR, fn), "r", encoding="utf-8") as f:
                        d = json.load(f)
                        out.append({
                            "group_id": d["group_id"],
                            "name": d.get("name"),
                            "device_count": len(d.get("targets", [])),
                            "updated_at": d.get("updated_at"),
                        })
                except Exception:
                    pass
        return sorted(out, key=lambda x: x.get("updated_at", ""), reverse=True)

    def delete(self, group_id: str) -> bool:
        path = self._path(group_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def add_device(self, group_id: str, target: dict) -> Optional[DeviceGroup]:
        group = self.get(group_id)
        if not group:
            return None
        group.targets.append(DeviceTarget.from_dict(target))
        self.save(group)
        return group

    def resolve_credentials(self, group: DeviceGroup) -> List[dict]:
        """Return a list of fully-resolved connection specs (with secrets from the
        vault) for every device in the group. Devices without a stored secret get
        secret=None and will be skipped at execution time."""
        try:
            from vault import VAULT
        except Exception:
            VAULT = None
        specs = []
        for t in group.targets:
            secret = None
            if VAULT and t.host:
                secret = VAULT.resolve(t.host)
            specs.append({
                "host": t.host,
                "conn_type": t.conn_type,
                "username": t.username,
                "port": t.port,
                "password": secret,
            })
        return specs


GROUP_MANAGER = DeviceGroupManager()
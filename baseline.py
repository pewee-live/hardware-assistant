"""Configuration baseline & drift detection.

Captures point-in-time snapshots of a device's configuration (ip addr, iptables,
route table, key config files) and diffs them against the previous snapshot so
the agent can immediately answer "what changed on this box?".

Snapshots are stored under data/baselines/<device_key>/ as timestamped JSON.
Each snapshot is a dict of {probe_name: captured_output}. The diff compares the
latest snapshot against the one before it (or an explicitly chosen baseline).
"""
import os
import json
import re
from datetime import datetime
from typing import Optional, Dict, List


BASELINES_DIR = os.path.join("data", "baselines")

# The default set of "probes" run for every snapshot. Each entry maps a probe
# name to a shell command. These are chosen for maximum diagnostic signal:
# network config, firewall rules, routing, mounts, and running services.
DEFAULT_PROBES = {
    "ip_addr": "ip addr show 2>/dev/null || ifconfig 2>/dev/null",
    "ip_route": "ip route show 2>/dev/null || route -n 2>/dev/null",
    "iptables": "iptables -L -n 2>/dev/null || echo 'iptables not available'",
    "resolv": "cat /etc/resolv.conf 2>/dev/null",
    "hosts": "cat /etc/hosts 2>/dev/null",
    "fstab": "cat /etc/fstab 2>/dev/null | grep -v '^#' | grep -v '^$'",
    "mounts": "mount 2>/dev/null | grep -v cgroup | grep -v tmpfs",
    "services": "systemctl list-units --type=service --state=running --no-pager 2>/dev/null || echo 'systemctl not available'",
    "sshd_config": "cat /etc/ssh/sshd_config 2>/dev/null | grep -v '^#' | grep -v '^$'",
    "hostname": "hostname 2>/dev/null",
    "uname": "uname -a 2>/dev/null",
}


class BaselineManager:
    """Stores and diffs device configuration snapshots."""

    def __init__(self):
        os.makedirs(BASELINES_DIR, exist_ok=True)

    @staticmethod
    def _safe_name(key: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", key or "unknown")

    def _device_dir(self, device_key: str) -> str:
        d = os.path.join(BASELINES_DIR, self._safe_name(device_key))
        os.makedirs(d, exist_ok=True)
        return d

    def _snapshot_path(self, device_key: str, timestamp: str) -> str:
        return os.path.join(self._device_dir(device_key), f"{timestamp}.json")

    def save_snapshot(self, device_key: str, probes: Dict[str, str]) -> dict:
        """Persist a snapshot. `probes` is {probe_name: output_text}.
        If a snapshot already exists for this exact second, append a counter
        so rapid successive snapshots don't clobber each other."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = self._device_dir(device_key)
        # Ensure uniqueness if multiple snapshots land in the same second.
        candidate = ts
        counter = 2
        while os.path.exists(os.path.join(d, candidate + ".json")):
            candidate = f"{ts}_{counter}"
            counter += 1
        ts = candidate
        snapshot = {
            "device_key": device_key,
            "timestamp": ts,
            "datetime": datetime.now().isoformat(),
            "probes": probes,
        }
        path = self._snapshot_path(device_key, ts)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        return snapshot

    def list_snapshots(self, device_key: str) -> List[dict]:
        """Return metadata for all snapshots of a device, newest first."""
        d = self._device_dir(device_key)
        out = []
        for fn in sorted(os.listdir(d), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                    snap = json.load(f)
                out.append({
                    "timestamp": snap.get("timestamp"),
                    "datetime": snap.get("datetime"),
                    "probe_count": len(snap.get("probes", {})),
                    "probe_names": list(snap.get("probes", {}).keys()),
                })
            except Exception:
                pass
        return out

    def get_snapshot(self, device_key: str, timestamp: str) -> Optional[dict]:
        path = self._snapshot_path(device_key, timestamp)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_latest_snapshot(self, device_key: str) -> Optional[dict]:
        snaps = self.list_snapshots(device_key)
        if not snaps:
            return None
        return self.get_snapshot(device_key, snaps[0]["timestamp"])

    def diff(self, device_key: str, newer_ts: Optional[str] = None,
             older_ts: Optional[str] = None) -> Optional[dict]:
        """Diff two snapshots. Defaults: newest vs the one before it.

        Returns a dict with per-probe added/removed/changed line sets, plus a
        human-readable summary. Returns None if fewer than 2 snapshots exist.
        """
        snaps = self.list_snapshots(device_key)
        if len(snaps) < 2:
            return None

        newer = self.get_snapshot(device_key, newer_ts) if newer_ts else \
                self.get_snapshot(device_key, snaps[0]["timestamp"])
        older = self.get_snapshot(device_key, older_ts) if older_ts else \
                self.get_snapshot(device_key, snaps[1]["timestamp"])

        if not newer or not older:
            return None

        newer_probes = newer.get("probes", {})
        older_probes = older.get("probes", {})
        all_probe_names = sorted(set(newer_probes.keys()) | set(older_probes.keys()))

        diff_details = {}
        changed_count = 0
        for name in all_probe_names:
            new_lines = set((newer_probes.get(name) or "").splitlines())
            old_lines = set((older_probes.get(name) or "").splitlines())
            added = sorted(new_lines - old_lines)
            removed = sorted(old_lines - new_lines)
            if added or removed:
                changed_count += 1
                diff_details[name] = {"added": added, "removed": removed}

        return {
            "device_key": device_key,
            "newer_snapshot": newer.get("timestamp"),
            "older_snapshot": older.get("timestamp"),
            "newer_datetime": newer.get("datetime"),
            "older_datetime": older.get("datetime"),
            "changed_probes": changed_count,
            "total_probes": len(all_probe_names),
            "details": diff_details,
        }

    @staticmethod
    def format_diff(diff_result: dict) -> str:
        """Render a diff result as human-readable text for the agent."""
        lines = [
            f"CONFIG DRIFT REPORT for {diff_result['device_key']}",
            f"Comparing: {diff_result['older_datetime']} -> {diff_result['newer_datetime']}",
            f"{diff_result['changed_probes']} of {diff_result['total_probes']} config areas changed.",
            "",
        ]
        details = diff_result["details"]
        if not details:
            lines.append("No configuration changes detected.")
            return "\n".join(lines)
        for name, changes in sorted(details.items()):
            lines.append(f"=== {name} ===")
            for line in changes["added"]:
                lines.append(f"  + {line}")
            for line in changes["removed"]:
                lines.append(f"  - {line}")
            lines.append("")
        return "\n".join(lines)


BASELINE_MANAGER = BaselineManager()
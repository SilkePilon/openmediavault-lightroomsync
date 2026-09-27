"""SQLite record of which asset was written to which file."""

import dataclasses
import os
import sqlite3
import time
from typing import Dict, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,       -- '<album id>:<asset id>', album id empty for library sync
    asset_id TEXT NOT NULL,
    relpath TEXT NOT NULL UNIQUE,
    updated TEXT NOT NULL,      -- the asset's `updated` timestamp when downloaded
    rendition TEXT NOT NULL,
    synced_at REAL NOT NULL
);
"""


@dataclasses.dataclass
class Entry:
    key: str
    asset_id: str
    relpath: str
    updated: str
    rendition: str
    synced_at: float = 0.0


def entry_key(album_id: str, asset_id: str) -> str:
    return f"{album_id}:{asset_id}"


class State:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def all(self) -> Dict[str, Entry]:
        rows = self.db.execute(
            "SELECT key, asset_id, relpath, updated, rendition, synced_at FROM files")
        return {r[0]: Entry(*r) for r in rows}

    def owner_of(self, relpath: str) -> Optional[str]:
        row = self.db.execute("SELECT key FROM files WHERE relpath = ?",
                              (relpath,)).fetchone()
        return row[0] if row else None

    def put(self, entry: Entry):
        entry.synced_at = time.time()
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                dataclasses.astuple(entry))

    def delete(self, key: str):
        with self.db:
            self.db.execute("DELETE FROM files WHERE key = ?", (key,))

    def reset(self):
        with self.db:
            self.db.execute("DELETE FROM files")

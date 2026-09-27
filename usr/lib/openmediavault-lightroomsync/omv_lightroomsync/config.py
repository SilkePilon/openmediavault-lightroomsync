"""Runtime configuration.

The file is rendered by the Salt deploy state from the openmediavault
configuration database, so the sync engine never talks to the database
itself and can be run and tested standalone.
"""

import dataclasses
import datetime
import json
import os
from typing import List

CONFIG_FILE = os.environ.get(
    "OMV_LIGHTROOMSYNC_CONFIG", "/etc/openmediavault-lightroomsync/config.json"
)
STATE_DIR = os.environ.get(
    "OMV_LIGHTROOMSYNC_STATE_DIR", "/var/lib/openmediavault-lightroomsync"
)

RENDITIONS = ("fullsize", "2560", "2048", "1280", "640")
SOURCES = ("all", "albums")
DELETE_MODES = ("keep", "delete", "trash")


class ConfigError(Exception):
    pass


@dataclasses.dataclass
class Config:
    # The shared folder; must already exist. `target_dir` is inside it.
    base_dir: str = ""
    target_dir: str = ""
    owner: str = ""
    group: str = "users"
    source: str = "all"
    albums: List[str] = dataclasses.field(default_factory=list)
    hide_stacked: bool = False
    captured_after: str = ""
    folder_template: str = "{year}/{year}-{month}"
    filename_template: str = "{year}{month}{day}_{hour}{minute}{second}_{name}"
    rendition: str = "2048"
    rendition_fallback: bool = True
    max_dimension: int = 0
    jpeg_quality: int = 92
    keep_metadata: bool = True
    set_mtime: bool = True
    redownload_edited: bool = True
    delete_mode: str = "keep"
    concurrency: int = 4

    def validate(self):
        if not self.target_dir or not os.path.isabs(self.target_dir):
            raise ConfigError("target_dir must be an absolute path")
        if self.source not in SOURCES:
            raise ConfigError(f"source must be one of {SOURCES}")
        if self.source == "albums" and not self.albums:
            raise ConfigError("source is 'albums' but no albums are selected")
        if self.rendition not in RENDITIONS:
            raise ConfigError(f"rendition must be one of {RENDITIONS}")
        if self.delete_mode not in DELETE_MODES:
            raise ConfigError(f"delete_mode must be one of {DELETE_MODES}")
        if not 1 <= self.concurrency <= 16:
            raise ConfigError("concurrency must be between 1 and 16")
        if not 1 <= self.jpeg_quality <= 100:
            raise ConfigError("jpeg_quality must be between 1 and 100")
        if self.max_dimension < 0:
            raise ConfigError("max_dimension must not be negative")
        if self.captured_after:
            try:
                datetime.date.fromisoformat(self.captured_after)
            except ValueError:
                raise ConfigError("captured_after must be a date like 2024-01-31") from None
        if not self.filename_template.strip():
            raise ConfigError("filename_template must not be empty")
        return self


def load(path: str = CONFIG_FILE) -> Config:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(
            f"{path} not found; enable the plugin and apply the configuration"
        ) from None
    known = {f.name for f in dataclasses.fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known}).validate()

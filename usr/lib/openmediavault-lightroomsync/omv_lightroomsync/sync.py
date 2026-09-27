"""One-way sync: Lightroom cloud -> local folder."""

import collections
import concurrent.futures
import dataclasses
import datetime
import grp
import logging
import os
import pwd
import shutil
import tempfile
import threading
import time
from typing import Dict, List, Tuple

from . import imaging, naming
from .api import LightroomClient
from .config import RENDITIONS, Config
from .state import Entry, State, entry_key

log = logging.getLogger(__name__)

# Renditions that the server only creates on request.
ON_DEMAND = ("fullsize", "2560")
GENERATION_TIMEOUT = 180
TRASH_DIR = ".lightroom-trash"
DIR_MODE = 0o2775
FILE_MODE = 0o664
# Refuse to delete more than this share of the synced files in one run
# unless forced: a broken listing must not wipe the local copy.
MASS_DELETE_RATIO = 0.5
MASS_DELETE_MIN = 20


class SyncError(Exception):
    pass


@dataclasses.dataclass
class Wanted:
    key: str
    asset: dict
    relpath: str


@dataclasses.dataclass
class Stats:
    listed: int = 0
    downloaded: int = 0
    updated: int = 0
    moved: int = 0
    unchanged: int = 0
    deleted: int = 0
    trashed: int = 0
    failed: int = 0
    renditions: Dict[str, int] = dataclasses.field(
        default_factory=collections.Counter)

    def summary(self) -> str:
        parts = [f"{self.listed} photos in Lightroom",
                 f"{self.downloaded} new", f"{self.updated} updated",
                 f"{self.moved} renamed", f"{self.unchanged} unchanged",
                 f"{self.deleted} deleted", f"{self.trashed} moved to trash",
                 f"{self.failed} failed"]
        if self.renditions:
            parts.append("renditions: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.renditions.items())))
        return "; ".join(parts)


def album_paths(client: LightroomClient, catalog_id: str) -> Dict[str, Tuple[str, str]]:
    """Map album id -> (name, 'Parent/Child/Name')."""
    albums = {a["id"]: a for a in client.albums(catalog_id)}

    def name(a):
        return a.get("payload", {}).get("name") or a["id"][:8]

    def path(a, depth=0):
        parent = a.get("payload", {}).get("parent", {}).get("id")
        if parent in albums and depth < 32:
            return path(albums[parent], depth + 1) + "/" + name(a)
        return name(a)

    return {aid: (name(a), path(a)) for aid, a in albums.items()}


class Syncer:
    def __init__(self, cfg: Config, client: LightroomClient, state: State,
                 dry_run: bool = False, allow_mass_delete: bool = False,
                 sleep=time.sleep, clock=time.monotonic):
        naming.validate_template(cfg.folder_template, "folder template")
        naming.validate_template(cfg.filename_template, "file name template")
        self.cfg = cfg
        self.client = client
        self.state = state
        self.dry_run = dry_run
        self.allow_mass_delete = allow_mass_delete
        self.sleep = sleep
        self.clock = clock
        self.stats = Stats()
        self._generation_denied = False
        self._dir_lock = threading.Lock()
        self._uid, self._gid = self._resolve_owner()

    # --- Listing -----------------------------------------------------------

    def _captured_ok(self, asset: dict) -> bool:
        if not self.cfg.captured_after:
            return True
        cutoff = datetime.datetime.fromisoformat(self.cfg.captured_after)
        return naming.capture_datetime(asset) >= cutoff

    def list_wanted(self, catalog_id: str) -> List[Tuple[str, dict, dict]]:
        """Return (key, asset, tokens) for everything that should exist locally."""
        wanted = []
        if self.cfg.source == "all":
            for asset in self.client.iter_assets(catalog_id, self.cfg.hide_stacked):
                self.stats.listed += 1
                if self._captured_ok(asset):
                    wanted.append((entry_key("", asset["id"]), asset,
                                   naming.asset_tokens(asset)))
            return wanted
        paths = album_paths(self.client, catalog_id)
        seen = set()
        for album_id in self.cfg.albums:
            if album_id not in paths:
                raise SyncError(f"Album {album_id} no longer exists in Lightroom; "
                                "update the album selection")
            album_name, album_path = paths[album_id]
            for asset in self.client.iter_album_assets(catalog_id, album_id):
                seen.add(asset["id"])
                if self._captured_ok(asset):
                    wanted.append((entry_key(album_id, asset["id"]), asset,
                                   naming.asset_tokens(asset, album_name, album_path)))
        self.stats.listed = len(seen)
        return wanted

    # --- Planning ----------------------------------------------------------

    def _abspath(self, relpath: str) -> str:
        return os.path.join(self.cfg.target_dir, relpath)

    def plan(self, wanted, existing: Dict[str, Entry]):
        """Split the wanted assets into downloads, renames and unchanged."""
        # Paths are claimed by existing entries first so that renames and
        # new files never overwrite a file another asset owns.
        claimed = {e.relpath: k for k, e in existing.items()}
        downloads, renames = [], []
        for key, asset, tokens in wanted:
            base = naming.render_relpath(self.cfg.folder_template,
                                         self.cfg.filename_template, tokens)
            old = existing.get(key)
            relpath = self._allocate(base, key, claimed)
            have_file = old is not None and os.path.exists(self._abspath(old.relpath))
            if not have_file:
                downloads.append((Wanted(key, asset, relpath), old))
                continue
            if self.cfg.redownload_edited and old.updated != asset.get("updated", ""):
                downloads.append((Wanted(key, asset, relpath), old))
            elif old.relpath != relpath:
                renames.append((Wanted(key, asset, relpath), old))
            else:
                self.stats.unchanged += 1
        return downloads, renames

    def _allocate(self, base: str, key: str, claimed: Dict[str, str]) -> str:
        # Old paths stay claimed for the whole run, even when their file is
        # about to move: renames run before downloads, and an updated file's
        # old path is only removed after its download, so handing that path
        # to another asset could delete the other asset's file.
        n = 1
        candidate = base
        while True:
            owner = claimed.get(candidate)
            if owner == key:
                return candidate
            if owner is None and not os.path.lexists(self._abspath(candidate)):
                claimed[candidate] = key
                return candidate
            n += 1
            candidate = naming.with_suffix(base, n)

    # --- Downloading -------------------------------------------------------

    def _rendition_chain(self) -> List[str]:
        if not self.cfg.rendition_fallback:
            return [self.cfg.rendition]
        return list(RENDITIONS[RENDITIONS.index(self.cfg.rendition):])

    def _fetch_on_demand(self, catalog_id: str, asset_id: str, rtype: str):
        data = self.client.download_rendition(catalog_id, asset_id, rtype)
        if data or self._generation_denied:
            return data
        status = self.client.generate_renditions(catalog_id, asset_id, [rtype])
        if status == 403:
            if not self._generation_denied:
                log.warning("Adobe refused to generate '%s' renditions (HTTP 403). "
                            "Your API key probably lacks access to full-size "
                            "renditions; falling back to smaller sizes.", rtype)
            self._generation_denied = True
            return None
        if status not in (200, 201, 202):
            return None
        deadline = self.clock() + GENERATION_TIMEOUT
        delay = 2
        while self.clock() < deadline:
            self.sleep(delay)
            if self.client.rendition_ready(catalog_id, asset_id, rtype):
                return self.client.download_rendition(catalog_id, asset_id, rtype)
            delay = min(delay * 2, 30)
        log.warning("Timed out waiting for the '%s' rendition of %s", rtype, asset_id)
        return None

    def fetch(self, catalog_id: str, asset: dict) -> Tuple[bytes, str]:
        for rtype in self._rendition_chain():
            if rtype in ON_DEMAND:
                data = self._fetch_on_demand(catalog_id, asset["id"], rtype)
            else:
                data = self.client.download_rendition(catalog_id, asset["id"], rtype)
            if data:
                return data, rtype
        raise SyncError("no rendition available (tried "
                        + ", ".join(self._rendition_chain()) + ")")

    def _resolve_owner(self):
        uid = gid = -1
        if self.cfg.owner:
            try:
                uid = pwd.getpwnam(self.cfg.owner).pw_uid
            except KeyError:
                log.warning("User '%s' does not exist; not changing file owner",
                            self.cfg.owner)
        if self.cfg.group:
            try:
                gid = grp.getgrnam(self.cfg.group).gr_gid
            except KeyError:
                pass
        return uid, gid

    def _chown(self, path: str):
        if (self._uid, self._gid) != (-1, -1) and os.geteuid() == 0:
            os.chown(path, self._uid, self._gid)

    def _makedirs(self, directory: str):
        """Create missing parent directories with the configured owner."""
        # Download workers create directories concurrently.
        with self._dir_lock:
            missing = []
            while not os.path.isdir(directory):
                missing.append(directory)
                directory = os.path.dirname(directory)
            for d in reversed(missing):
                try:
                    os.mkdir(d, DIR_MODE)
                except FileExistsError:
                    continue
                os.chmod(d, DIR_MODE)  # mkdir's mode is filtered by the umask
                self._chown(d)

    def _write(self, relpath: str, data: bytes, asset: dict):
        path = self._abspath(relpath)
        self._makedirs(os.path.dirname(path))
        fd, tmp = tempfile.mkstemp(prefix=".lrsync-", suffix=".part",
                                   dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, FILE_MODE)
            self._chown(tmp)
            if self.cfg.set_mtime:
                ts = naming.capture_datetime(asset).timestamp()
                os.utime(tmp, (ts, ts))
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _download_one(self, catalog_id: str, item: Wanted) -> str:
        data, rtype = self.fetch(catalog_id, item.asset)
        data = imaging.process(data, self.cfg.max_dimension, self.cfg.jpeg_quality,
                               self.cfg.keep_metadata)
        self._write(item.relpath, data, item.asset)
        return rtype

    # --- Removal -------------------------------------------------------------

    def _remove(self, entry: Entry):
        path = self._abspath(entry.relpath)
        if self.cfg.delete_mode == "trash":
            dest = os.path.join(self.cfg.target_dir, TRASH_DIR, entry.relpath)
            n = 1
            while os.path.lexists(dest):
                n += 1
                dest = naming.with_suffix(
                    os.path.join(self.cfg.target_dir, TRASH_DIR, entry.relpath), n)
            if os.path.exists(path):
                self._makedirs(os.path.dirname(dest))
                shutil.move(path, dest)
            self.stats.trashed += 1
        else:
            if os.path.exists(path):
                os.unlink(path)
            self.stats.deleted += 1
        self._prune_empty_dirs(os.path.dirname(path))

    def _prune_empty_dirs(self, directory: str):
        root = os.path.realpath(self.cfg.target_dir)
        directory = os.path.realpath(directory)
        while directory.startswith(root + os.sep):
            try:
                os.rmdir(directory)
            except OSError:
                return
            directory = os.path.dirname(directory)

    # --- Orchestration -----------------------------------------------------

    def run(self) -> Stats:
        if self.cfg.base_dir and not os.path.isdir(self.cfg.base_dir):
            # Never create the shared folder itself: if its disk is not
            # mounted this would silently fill the root file system.
            raise SyncError(f"Shared folder {self.cfg.base_dir} is not available; "
                            "is its file system mounted?")
        if not os.path.isdir(self.cfg.target_dir):
            if self.dry_run:
                log.info("Would create %s", self.cfg.target_dir)
            else:
                self._makedirs(self.cfg.target_dir)
        catalog_id = self.client.catalog_id()
        log.info("Listing photos in Lightroom ...")
        wanted = self.list_wanted(catalog_id)
        existing = self.state.all()
        downloads, renames = self.plan(wanted, existing)
        wanted_keys = {k for k, _, _ in wanted}
        stale = [e for k, e in existing.items() if k not in wanted_keys]
        log.info("%d photos selected: %d to download, %d to rename, %d no longer "
                 "in Lightroom", len(wanted), len(downloads), len(renames), len(stale))

        for item, old in renames:
            log.info("Rename %s -> %s", old.relpath, item.relpath)
            if not self.dry_run:
                self._makedirs(os.path.dirname(self._abspath(item.relpath)))
                os.replace(self._abspath(old.relpath), self._abspath(item.relpath))
                self._prune_empty_dirs(os.path.dirname(self._abspath(old.relpath)))
                self.state.put(dataclasses.replace(old, relpath=item.relpath))
            self.stats.moved += 1

        self._download_all(catalog_id, downloads)
        self._handle_stale(stale, len(existing))
        return self.stats

    def _download_all(self, catalog_id: str, downloads):
        if self.dry_run:
            for item, old in downloads:
                log.info("Would %s %s", "update" if old else "download", item.relpath)
                if old:
                    self.stats.updated += 1
                else:
                    self.stats.downloaded += 1
            return
        total = len(downloads)
        with concurrent.futures.ThreadPoolExecutor(self.cfg.concurrency) as pool:
            futures = {pool.submit(self._download_one, catalog_id, item): (item, old)
                       for item, old in downloads}
            for done, future in enumerate(
                    concurrent.futures.as_completed(futures), start=1):
                item, old = futures[future]
                try:
                    rtype = future.result()
                except Exception as e:  # keep going; one bad asset must not stop the run
                    self.stats.failed += 1
                    log.error("[%d/%d] %s: %s", done, total, item.relpath, e)
                    continue
                # SQLite writes stay on this thread.
                if old and old.relpath != item.relpath and \
                        os.path.exists(self._abspath(old.relpath)):
                    os.unlink(self._abspath(old.relpath))
                    self._prune_empty_dirs(os.path.dirname(self._abspath(old.relpath)))
                self.state.put(Entry(item.key, item.asset["id"], item.relpath,
                                     item.asset.get("updated", ""), rtype))
                self.stats.renditions[rtype] += 1
                if old:
                    self.stats.updated += 1
                else:
                    self.stats.downloaded += 1
                log.info("[%d/%d] %s %s (%s)", done, total,
                         "Updated" if old else "Downloaded", item.relpath, rtype)

    def _handle_stale(self, stale: List[Entry], tracked: int):
        if not stale:
            return
        if self.cfg.delete_mode == "keep":
            # The entries stay in the state so a photo that is added back
            # is recognised instead of being downloaded a second time.
            log.info("Keeping %d local files whose photos are no longer "
                     "selected in Lightroom", len(stale))
            return
        if (not self.allow_mass_delete and len(stale) >= MASS_DELETE_MIN
                and len(stale) > tracked * MASS_DELETE_RATIO):
            raise SyncError(
                f"Refusing to remove {len(stale)} of {tracked} synced files in one "
                "run. If this is intended, run 'omv-lightroomsync sync "
                "--allow-mass-delete' once.")
        verb = "move to trash" if self.cfg.delete_mode == "trash" else "delete"
        for entry in stale:
            if self.dry_run:
                log.info("Would %s %s", verb, entry.relpath)
                continue
            try:
                self._remove(entry)
                self.state.delete(entry.key)
                log.info("Removed %s (%s)", entry.relpath, verb)
            except OSError as e:
                self.stats.failed += 1
                log.error("Could not %s %s: %s", verb, entry.relpath, e)

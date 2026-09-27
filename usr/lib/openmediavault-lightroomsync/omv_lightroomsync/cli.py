"""Command line interface: `omv-lightroomsync <command>`.

Secrets are read as JSON from stdin rather than from arguments so they do
not show up in the process list or in error messages.
"""

import argparse
import contextlib
import datetime
import fcntl
import json
import logging
import os
import sys
import time

from . import __version__, naming
from .api import ApiError, LightroomClient
from .auth import AuthError, TokenStore, authorize_url
from .config import STATE_DIR, ConfigError, load
from .state import State
from .sync import SyncError, Syncer, album_paths

AUTH_FILE = os.path.join(STATE_DIR, "auth.json")
STATE_FILE = os.path.join(STATE_DIR, "state.db")
STATUS_FILE = os.path.join(STATE_DIR, "status.json")
LOCK_FILE = os.path.join(STATE_DIR, "sync.lock")
# Warn this long before the Adobe sign-in is expected to expire.
EXPIRY_WARNING = 3 * 24 * 3600

log = logging.getLogger("omv_lightroomsync")


def _write_status(data: dict):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, STATUS_FILE)


def _read_status() -> dict:
    try:
        with open(STATUS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _sync_lock():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    with open(LOCK_FILE, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError("Another sync is already running") from None
        yield


def cmd_sync(args) -> int:
    started = time.time()
    status = {"started": started, "dry_run": args.dry_run}
    try:
        with _sync_lock():
            cfg = load()
            tokens = TokenStore(AUTH_FILE)
            state = State(STATE_FILE)
            try:
                if args.reset_state and not args.dry_run:
                    log.info("Forgetting previously synced files")
                    state.reset()
                syncer = Syncer(cfg, LightroomClient(tokens), state,
                                dry_run=args.dry_run,
                                allow_mass_delete=args.allow_mass_delete)
                stats = syncer.run()
            finally:
                state.close()
    except (ConfigError, AuthError, ApiError, SyncError, OSError) as e:
        log.error("Sync failed: %s", e)
        if not args.dry_run:
            _write_status(dict(status, finished=time.time(), result="failed",
                               message=str(e)))
        return 1
    result = "partial" if stats.failed else "success"
    log.info("Sync %s: %s", "finished" if result == "success"
             else "finished with errors", stats.summary())
    if not args.dry_run:
        _write_status(dict(status, finished=time.time(), result=result,
                           message=stats.summary()))
    return 2 if stats.failed else 0


def _fmt_time(ts) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def cmd_status(args) -> int:
    tokens = TokenStore(AUTH_FILE)
    status = _read_status()
    synced = 0
    if os.path.exists(STATE_FILE):
        state = State(STATE_FILE)
        synced = len(state.all())
        state.close()
    if args.json:
        json.dump({
            "authorized": tokens.authorized,
            "account": tokens.data.get("account_email", ""),
            "refresh_expires_at": tokens.refresh_expires_at(),
            "synced_files": synced,
            "last_run": status,
        }, sys.stdout)
        return 0
    lines = []
    if tokens.authorized:
        lines.append("Adobe account:  signed in as "
                     + (tokens.data.get("account_email") or "(unknown)"))
        expires = tokens.refresh_expires_at()
        if expires:
            remaining = expires - time.time()
            note = ""
            if remaining < 0:
                note = "  <-- may have expired, sign in again if syncs fail"
            elif remaining < EXPIRY_WARNING:
                note = "  <-- expires soon, sign in again"
            lines.append(f"Sign-in valid:  until about {_fmt_time(expires)}{note}")
    else:
        lines.append("Adobe account:  NOT SIGNED IN (see the Account page)")
    if status:
        lines.append(f"Last sync:      {_fmt_time(status['started'])} - "
                     f"{status.get('result', 'running')}")
        if status.get("finished"):
            lines.append("Duration:       "
                         f"{int(status['finished'] - status['started'])} s")
        if status.get("message"):
            lines.append(f"Result:         {status['message']}")
    else:
        lines.append("Last sync:      never")
    lines.append(f"Synced files:   {synced}")
    print("\n".join(lines))
    return 0


def cmd_albums(args) -> int:
    tokens = TokenStore(AUTH_FILE)
    if not tokens.authorized:
        print("[]" if args.json else "Not signed in")
        return 0 if args.json else 1
    client = LightroomClient(tokens)
    paths = album_paths(client, client.catalog_id())
    albums = sorted(({"id": aid, "name": path} for aid, (_, path) in paths.items()),
                    key=lambda a: a["name"].lower())
    if args.json:
        json.dump(albums, sys.stdout)
    else:
        for a in albums:
            print(f"{a['id']}  {a['name']}")
    return 0


def cmd_auth_url(args) -> int:
    print(authorize_url(args.client_id, args.redirect_uri))
    return 0


def cmd_auth_complete(args) -> int:
    try:
        params = json.load(sys.stdin)
        client_id = params["client_id"]
        secret = params["client_secret"]
        redirect_uri = params["redirect_uri"]
        response = params["response"]
    except (ValueError, KeyError, TypeError) as e:
        raise AuthError(f"Invalid input on stdin: {e}") from None
    tokens = TokenStore(AUTH_FILE)
    tokens.complete(client_id, secret, redirect_uri, response)
    try:
        account = LightroomClient(tokens).account()
        tokens.data["account_email"] = account.get("email", "")
        tokens.save()
        entitlement = account.get("entitlement", {}).get("status")
        if entitlement not in (None, "subscriber", "trial"):
            log.warning("This Adobe account has no active Lightroom plan "
                        "(status: %s)", entitlement)
    except ApiError as e:
        log.warning("Signed in, but reading the account failed: %s", e)
    print("Signed in as " + (tokens.data.get("account_email") or "(unknown)"))
    return 0


def cmd_auth_logout(args) -> int:
    TokenStore(AUTH_FILE).clear()
    print("Signed out")
    return 0


def cmd_tokens(args) -> int:
    for token, desc in naming.TOKENS.items():
        print(f"{{{token}}}".ljust(14) + desc)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="omv-lightroomsync",
        description="Sync Adobe Lightroom photos to a local folder.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="run a sync now")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="only show what would be done")
    p.add_argument("--allow-mass-delete", action="store_true",
                   help="allow removing more than half of the synced files")
    p.add_argument("--reset-state", action="store_true",
                   help="forget which files were synced (files already on "
                        "disk are kept, new copies get a suffix)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("status", help="show sign-in and last sync status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("albums", help="list Lightroom albums")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_albums)

    p = sub.add_parser("auth-url", help="print the Adobe sign-in URL")
    p.add_argument("--client-id", required=True)
    p.add_argument("--redirect-uri", required=True)
    p.set_defaults(func=cmd_auth_url)

    p = sub.add_parser(
        "auth-complete",
        help="exchange the pasted redirect URL for tokens; reads "
             '{"client_id", "client_secret", "redirect_uri", "response"} '
             "as JSON from stdin")
    p.set_defaults(func=cmd_auth_complete)

    p = sub.add_parser("auth-logout", help="delete the stored Adobe tokens")
    p.set_defaults(func=cmd_auth_logout)

    p = sub.add_parser("tokens", help="list the naming template tokens")
    p.set_defaults(func=cmd_tokens)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s", stream=sys.stdout)
    os.umask(0o022)
    try:
        return args.func(args)
    except (AuthError, ApiError, ConfigError) as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Interactive Telegram USER-account login + persistent session manager.

Use (either works):
  python -m recovery_v2.login_accounts
  python telegram_recovery_v2/scripts/login_telegram_accounts.py

NOTE: This authenticates normal MTProto USER accounts (telegram + api_id
/api_hash), NOT bots (no BotFather token). Sessions persist so both accounts
can reconnect WITHOUT requesting an OTP.

Security invariants enforced here:
  - session dir 0700, session files 0600, sqlite db 0600 (permissions checked/warned)
  - OTP / 2FA password / api_hash / session string are NEVER stored or echoed
  - 2FA password uses hidden input (getpass)
  - account DB stores only non-secret metadata
"""
from __future__ import annotations

import getpass
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    from telethon import TelegramClient
    from telethon.errors import (
        AuthKeyUnregisteredError, FloodWaitError, PasswordHashInvalidError,
        PhoneCodeExpiredError, PhoneCodeInvalidError, PhoneNumberInvalidError,
        SessionPasswordNeededError,
    )
    from telethon.network import ConnectionTcpFull
    from telethon.sessions import StringSession
except Exception:  # noqa: BLE001
    TelegramClient = None

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SESSIONS_DIR = DATA / "sessions"
DB_PATH = DATA / "recovery.sqlite3"

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

# user-friendly messages; NEVER surface secrets
FRIENDLY = {
    PhoneNumberInvalidError: "That phone number is not valid/allowed by Telegram.",
    PhoneCodeInvalidError: "The code you entered is incorrect.",
    PhoneCodeExpiredError: "That code has expired. Request a new one and retry.",
    SessionPasswordNeededError: "Two-step verification is enabled; a 2FA password is required.",
    PasswordHashInvalidError: "Wrong 2FA password.",
    AuthKeyUnregisteredError: "The stored session is no longer registered (revoked/expired).",
    FloodWaitError: "Telegram is throttling this number.",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_e164(phone: str) -> bool:
    return bool(phone and E164_RE.match(phone.strip()))


def normalize_phone(phone: str) -> str:
    """Collapse spaces/dashes; keep leading '+'. Raise if not E.164-looking."""
    p = phone.strip().replace(" ", "").replace("-", "")
    if not p.startswith("+"):
        if p.isdigit() and len(p) >= 10:
            p = "+" + p
    if not is_e164(p):
        raise ValueError(f"phone {phone!r} is not in E.164 format (e.g. +989394430100)")
    return p


# ---------------------------------------------------------------------------
# account database (non-secret metadata only)
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    telegram_user_id INTEGER,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    session_path TEXT,
    authorized INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    last_verified_at TEXT
);
"""


class AccountStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._chmod(self.db_path.parent, 0o700)
        self._chmod(self.db_path, 0o600)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def _chmod(p: Path, mode: int) -> None:
        try:
            os.chmod(p, mode)
        except OSError:
            pass

    def upsert(self, phone: str, telegram_user_id=None, first_name=None,
               last_name=None, username=None, session_path=None, authorized=True) -> None:
        self._conn.execute(
            """INSERT INTO telegram_accounts
               (phone, telegram_user_id, first_name, last_name, username,
                session_path, authorized, created_at, updated_at, last_verified_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET
                 telegram_user_id=excluded.telegram_user_id,
                 first_name=excluded.first_name, last_name=excluded.last_name,
                 username=excluded.username, session_path=excluded.session_path,
                 authorized=excluded.authorized, updated_at=excluded.updated_at,
                 last_verified_at=excluded.last_verified_at""",
            (phone, telegram_user_id, first_name, last_name, username, session_path,
             int(authorized), now_iso(), now_iso(), now_iso()),
        )
        self._conn.commit()

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, phone, telegram_user_id, first_name, last_name, username, "
            "session_path, authorized, last_verified_at FROM telegram_accounts "
            "ORDER BY id").fetchall()
        return [dict(zip(("id", "phone", "user_id", "first_name", "last_name",
                          "username", "session_path", "authorized", "last_verified_at"), r))
                for r in rows]

    def get(self, account_id: int) -> dict | None:
        for a in self.list():
            if a["id"] == account_id:
                return a
        return None

    def delete(self, account_id: int) -> None:
        self._conn.execute("DELETE FROM telegram_accounts WHERE id=?", (account_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# session files (StringSession persisted to a 0600 file per account)
# ---------------------------------------------------------------------------
def session_path_for(phone: str) -> Path:
    return SESSIONS_DIR / (f"account_{phone.replace('+', 'p')}.session")


def ensure_session_dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _secure(SESSIONS_DIR, 0o700)


def _secure(p: Path, want: int) -> None:
    try:
        os.chmod(p, want)
    except OSError:
        pass


def check_permissions() -> list[str]:
    """Warn when session/db permissions are unsafe. Returns messages."""
    warnings = []
    for p, want in ((SESSIONS_DIR, 0o700),
                    (DB_PATH.parent, 0o700), (DB_PATH, 0o600)):
        if not p.exists():
            continue
        try:
            mode = os.stat(p).st_mode & 0o777
            if mode > want:
                warnings.append(f"permissions on {p} are {oct(mode)}, expected <={oct(want)}")
        except OSError:
            pass
    for f in SESSIONS_DIR.glob("*.session"):
        try:
            if os.stat(f).st_mode & 0o777 > 0o600:
                warnings.append(f"permissions on {f} are unsafe (expected 0600)")
        except OSError:
            pass
    return warnings


# ---------------------------------------------------------------------------
# login engine (client-factory injectable for hermetic tests)
# ---------------------------------------------------------------------------
def default_client_factory(api_id: int, api_hash: str, session_string: str | None):
    """Build a Telethon TelegramClient from StringSession (never a bot token)."""
    session = StringSession(session_string) if session_string else StringSession()
    return TelegramClient(session, api_id, api_hash,
                          device_model="Telegram Recovery v2",
                          app_version="0.1.0", lang_code="en",
                          system_lang_code="en")


async def perform_login(phone: str, api_id: int, api_hash: str,
                        client_factory=default_client_factory,
                        prompt_code: Callable[[str], str] = lambda label: input(label),
                        prompt_password: Callable[[str], str] = lambda label: getpass.getpass(label),
                        existing_session: str | None = None) -> tuple[str, dict]:
    """Authenticate a USER account. Returns (session_string, me_dict)."""
    if api_id is None or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH are not set (check .env)")
    client = client_factory(api_id, api_hash, existing_session)
    await client.connect()

    try:
        if existing_session and await client.is_user_authorized():
            me = await client.get_me()
            return client.session.save(), _user_dict(me)
        # unlike bots, user accounts need the OTP flow
        if not await client.is_user_authorized():
            sent = await client.send_code_request(phone)  # noqa: F841
            code = prompt_code("\nEnter the code:\n> ").strip()
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                pw = prompt_password("\nTwo-step verification is enabled.\n"
                                     "Enter your Telegram 2FA password:\n> ")
                await client.sign_in(password=pw)  # NOTE: pw not stored/printed
        me = await client.get_me()
        return client.session.save(), _user_dict(me)
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _user_dict(me) -> dict:
    return {
        "id": getattr(me, "id", None),
        "first_name": getattr(me, "first_name", None),
        "last_name": getattr(me, "last_name", None),
        "username": getattr(me, "username", None),
        "phone": getattr(me, "phone", None),
    }


async def session_persistence_test(api_id: int, api_hash: str, session_string: str,
                                   client_factory=default_client_factory) -> bool:
    """Reconnect using ONLY the saved session string; must NOT request OTP."""
    if not session_string:
        return False
    client = client_factory(api_id, api_hash, session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return False
        await client.get_me()  # proves the auth key is live
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def check_session(api_id: int, api_hash: str, session_string: str,
                        client_factory=default_client_factory) -> dict:
    """Test an existing session: authorized? dialogs count? (get_me via session only)."""
    client = client_factory(api_id, api_hash, session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"valid": False, "reason": "not authorized / re-auth required"}
        me = await client.get_me()
        dialogs = 0
        try:
            dialogs = await client.get_dialogs(limit=200)
            dialogs = len(dialogs)
        except Exception:  # noqa: BLE001
            dialogs = -1
        return {"valid": True, "user": _user_dict(me), "dialogs": dialogs}
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "reason": _friendly(exc)}
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _safe(s: str) -> bool:
    """True when a string is safe to echo (no obvious secret patterns)."""
    if not s:
        return True
    lower = s.lower()
    if "api_" in lower or "session" in lower or "hash" in lower or "token" in lower:
        return False
    return len(s) < 400


def _friendly(exc: Exception) -> str:
    for cls, msg in FRIENDLY.items():
        if isinstance(exc, cls):
            if isinstance(exc, FloodWaitError):
                secs = getattr(exc, "seconds", None)
                return msg + (f" Wait {secs}s." if secs else "")
            return msg
    return f"Error: {type(exc).__name__}" + (f": {exc}" if _safe(str(exc)) else "")


# ---------------------------------------------------------------------------
# interactive terminal UI
# ---------------------------------------------------------------------------
def _env_api() -> tuple[int | None, str | None]:
    """ONE shared app credential for ALL accounts (TELEGRAM_API_ID/HASH only)."""
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    api_id = int(api_id) if api_id and api_id.isdigit() else None
    return api_id, api_hash


def _print_accounts(accounts) -> None:
    print("\nID   PHONE             NAME              USER ID       STATUS")
    for a in accounts:
        name = (a.get("first_name") or "") + (" " + (a.get("last_name") or "") if a.get("last_name") else "")
        status = "AUTHORIZED" if a.get("authorized") else "PENDING"
        print(f"{a['id']:<5}{a.get('phone',''):<18}{name:<17}{str(a.get('user_id') or ''):<14}{status}")


async def _add_or_reauth(store: AccountStore, api_id, api_hash,
                         reauth: bool = False, phone: str | None = None) -> None:
    if phone:
        try:
            phone = normalize_phone(phone)
        except ValueError as exc:
            print(f"\n{exc}"); return
    else:
        try:
            phone = normalize_phone(input("\nEnter Telegram phone number (E.164, e.g. +989394430100):\n> "))
        except ValueError as exc:
            print(f"\n{exc}"); return
    existing = next((a for a in store.list() if a.get("phone") == phone), None)
    existing_session = None
    if existing and existing.get("session_path"):
        pp = Path(existing["session_path"])
        if pp.exists():
            existing_session = pp.read_text(encoding="utf-8").strip()

    print("Connecting to Telegram...")
    try:
        session_str, me = await perform_login(phone, api_id, api_hash,
                                              existing_session=existing_session)
    except Exception as exc:  # noqa: BLE001
        print(f"\nLOGIN FAILED: {_friendly(exc)}")
        return

    print("\nLogin successful.")
    print(f"Telegram User ID: {me['id']}")
    print(f"First name: {me['first_name']}")
    print(f"Last name: {me.get('last_name') or ''}")
    print(f"Username: {me.get('username') or ''}")
    print(f"Phone: {me.get('phone') or phone}")

    ensure_session_dir()
    spath = session_path_for(phone)
    spath.write_text(session_str, encoding="utf-8")
    _secure(spath, 0o600)
    store.upsert(phone, telegram_user_id=me["id"], first_name=me["first_name"],
                 last_name=me.get("last_name"), username=me.get("username"),
                 session_path=str(spath), authorized=True)

    ok = await session_persistence_test(api_id, api_hash, session_str)
    print(f"\nSESSION PERSISTENCE TEST: {'PASS' if ok else 'FAIL'}")
    print(f"Saved session: {spath}")


async def _test_session(store: AccountStore, api_id, api_hash) -> None:
    accounts = [a for a in store.list() if a.get("session_path")]
    if not accounts:
        print("\nNo sessions saved yet."); return
    _print_accounts(accounts)
    try:
        idx = int(input("\nSelect account ID to test:\n> "))
    except ValueError:
        print("Invalid selection."); return
    acc = store.get(idx)
    if not acc or not acc.get("session_path"):
        print("Account not found."); return
    pp = Path(acc["session_path"])
    if not pp.exists():
        print("SESSION INVALID / RE-AUTH REQUIRED (no session file)"); return
    res = await check_session(api_id, api_hash, pp.read_text(encoding="utf-8").strip())
    if res.get("valid"):
        store.upsert(acc["phone"], authorized=True, last_verified_at=now_iso(),
                     session_path=acc["session_path"])
        print(f"\nSESSION VALID  user={res['user']['id']} dialogs={res.get('dialogs')}")
    else:
        store.upsert(acc["phone"], authorized=False, session_path=acc["session_path"])
        print(f"\nSESSION INVALID / RE-AUTH REQUIRED  ({res.get('reason')})")


async def menu_loop(store: AccountStore, api_id, api_hash) -> None:
    while True:
        print("\n================================================================")
        print(" Telegram Recovery v2 - Account Login")
        print("================================================================")
        accounts = store.list()
        if accounts:
            print("\nExisting accounts:")
            for a in accounts:
                name = (a.get("first_name") or "") or (a.get("username") or "")
                status = "AUTHORIZED" if a.get("authorized") else "PENDING"
                print(f"[{a['id']}] {name:<18} {a.get('phone',''):<18} {status}")
        else:
            print("\nNo accounts yet.")
        print("\nSelect:\n1. Add new Telegram account\n2. Re-authenticate account\n"
              "3. Test existing session\n4. List accounts\n5. Exit\n6. Remove account")
        choice = input("\nChoice: ").strip()
        if choice == "1":
            await _add_or_reauth(store, api_id, api_hash, reauth=False)
        elif choice == "2":
            await _add_or_reauth(store, api_id, api_hash, reauth=True)
        elif choice == "3":
            await _test_session(store, api_id, api_hash)
        elif choice == "4":
            _print_accounts(store.list())
        elif choice == "5":
            print("\nBye."); return
        elif choice == "6":
            await _remove_account(store, api_id, api_hash)
        else:
            print("Invalid choice.")


async def _remove_account(store: AccountStore, api_id, api_hash) -> None:
    _print_accounts(store.list())
    if not store.list():
        print("\nNo accounts to remove."); return
    try:
        idx = int(input("\nSelect account ID to remove:\n> "))
    except ValueError:
        print("Invalid selection."); return
    acc = store.get(idx)
    if not acc:
        print("Account not found."); return
    spath = acc.get("session_path")
    if spath and Path(spath).exists():
        sess = Path(spath).read_text(encoding="utf-8").strip()
        # best-effort server-side logout using the saved session (no OTP needed)
        try:
            client = default_client_factory(api_id, api_hash, sess)
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        Path(spath).unlink(missing_ok=True)
    store.delete(idx)
    print(f"\nRemoved account {acc.get('phone')} (session {spath or 'none'}).")


async def one_shot_login(store: AccountStore, api_id, api_hash, phone: str) -> int:
    """Direct (non-menu) login for ONE phone — reserved for future use."""
    try:
        phone = normalize_phone(phone)
    except ValueError as exc:
        print(f"{exc}"); return 1
    await _add_or_reauth(store, api_id, api_hash, reauth=False, phone=phone)
    return 0


def run(argv=None) -> int:
    """Menu-driven. No script arguments — everything is chosen inside the menu."""
    from recovery.config import load_dotenv
    load_dotenv()  # read .env (incl. TELEGRAM_API_ID / TELEGRAM_API_HASH)
    ensure_session_dir()
    store = AccountStore()
    try:
        api_id, api_hash = _env_api()
        if api_id is None or not api_hash:
            print("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env "
                  "(see telegram_recovery_v2/.env.example).")
            return 1
        for warn in check_permissions():
            print(f"[warn] {warn}")
        import asyncio
        try:
            asyncio.run(menu_loop(store, api_id, api_hash))
        except EOFError:
            print("\n[ended]")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(run())
"""Hermetic tests for recovery_v2.login_accounts (fake Telethon client)."""
from __future__ import annotations

import asyncio
import types

from telethon.errors import SessionPasswordNeededError

from recovery_v2 import login_accounts as L


class _Me:
    id = 55555
    first_name = "First"
    last_name = "Dev"
    username = "first_dev"
    phone = "+989394430100"


class FakeTL:
    """Minimal Telethon-client stand-in exercising the login branch logic."""

    def __init__(self, auth=False, needs_2fa=False):
        self.auth = auth
        self.needs_2fa = needs_2fa
        self.code_requested = False
        self.connected = False
        self.session = types.SimpleNamespace(save=lambda: "FK_SESSION")

    async def connect(self): self.connected = True
    async def disconnect(self): self.connected = False
    async def is_user_authorized(self): return self.auth
    async def send_code_request(self, phone): self.code_requested = True
    async def get_me(self): return _Me()
    async def get_dialogs(self, limit=200): return [object()] * min(limit, 3)

    async def sign_in(self, phone=None, code=None, password=None):
        if password is not None:
            self.auth = True
            return _Me()
        if self.needs_2fa:
            raise SessionPasswordNeededError(request=None)
        self.auth = True
        return _Me()


def _factory(**kw):
    def cf(api_id, api_hash, session_string=None):
        # a saved session implies an already-authorized client (persistence proof)
        return FakeTL(auth=bool(session_string), **kw)
    return cf


def test_is_e164_and_normalize():
    assert L.is_e164("+989394430100")
    assert not L.is_e164("989394430100")           # must start with +
    assert not L.is_e164("+abc")                   # malformed
    assert L.normalize_phone("+98 939 443 0100") == "+989394430100"
    try:
        L.normalize_phone("not-a-phone")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_perform_login_otp_only(tmp_path):
    code_prompt = lambda label: "12345"          # noqa: E731
    session, me = asyncio.run(L.perform_login(
        "+989394430100", 111, "hash", client_factory=_factory(),
        prompt_code=code_prompt))
    assert session == "FK_SESSION"
    assert me["id"] == 55555 and me["first_name"] == "First"


def test_perform_login_2fa_prompts_password():
    calls = []
    def pw_prompt(label):                          # noqa: E731
        calls.append(label)
        return "secret-password"
    session, me = asyncio.run(L.perform_login(
        "+989394430100", 111, "hash", client_factory=_factory(needs_2fa=True),
        prompt_code=lambda _: "12345", prompt_password=pw_prompt))
    assert session == "FK_SESSION" and me["id"] == 55555
    assert calls and "2FA" in calls[0]


def test_perform_login_reuses_valid_existing_session():
    # factory marks a session as authorized => no code request (no OTP)
    seen = []
    def cf(api_id, api_hash, session_string=None):
        seen.append(session_string)
        return FakeTL(auth=bool(session_string))
    session, me = asyncio.run(L.perform_login(
        "+989394430100", 111, "hash", client_factory=cf,
        existing_session="SAVED_SESSION_STRING"))
    assert session == "FK_SESSION"
    assert me["id"] == 55555
    assert "SAVED_SESSION_STRING" in seen  # reconnect used ONLY the saved session


def test_session_persistence_test_true_and_false():
    assert asyncio.run(L.session_persistence_test(111, "h", "SAVED", _factory())) is True
    assert asyncio.run(L.session_persistence_test(111, "h", "", _factory())) is False


def test_check_session_valid_reports_dialogs():
    res = asyncio.run(L.check_session(111, "h", "SAVED", _factory()))
    assert res["valid"] is True and res["dialogs"] == 3
    assert res["user"]["id"] == 55555


def test_account_store_no_secrets(tmp_path):
    db = tmp_path / "recovery.sqlite3"
    store = L.AccountStore(db)
    store.upsert("+989394430100", telegram_user_id=55555, first_name="First",
                 last_name="Dev", username="first_dev",
                 session_path="/x/account.session", authorized=True)
    store.upsert("+5511991966422", telegram_user_id=66666, first_name="David",
                 session_path="/x/account_b.session", authorized=True)
    rows = store.list()
    assert len(rows) == 2
    cols = [r for r in store._conn.execute("PRAGMA table_info(telegram_accounts)")]
    colnames = {c[1] for c in cols}
    # no secret-bearing columns allowed
    assert not ({"otp", "password", "api_hash", "session_string"} & colnames)


def test_session_path_deterministic():
    a = L.session_path_for("+989394430100")
    assert a.name.startswith("account_p989394430100") and a.suffix == ".session"


def test_upsert_accepts_last_verified_at_no_typeerror(tmp_path):
    """Regression: Choice 3's store.upsert(..., last_verified_at=...) must work."""
    db = tmp_path / "db.sqlite3"
    store = L.AccountStore(db)
    probe = "2026-09-01T10:00:00+00:00"
    store.upsert("+989394430100", telegram_user_id=165649921, authorized=True,
                 last_verified_at=probe)  # was: TypeError before the fix
    row = store.get(1)
    assert bool(row["authorized"]) is True
    assert row["last_verified_at"] == probe
    # default (no kwarg) still fills now_iso()
    store.upsert("+989394430100", telegram_user_id=165649921, authorized=True)
    assert bool(store.get(1)["authorized"]) is True


def test_menu_choice3_test_session_completes(tmp_path, monkeypatch):
    """Exact Choice 3 path (Account 1) must not raise TypeError."""
    db = tmp_path / "db.sqlite3"
    store = L.AccountStore(db)
    sess = tmp_path / "account_p989394430100.session"
    sess.write_text("SESSION_X", encoding="utf-8")
    store.upsert("+989394430100", telegram_user_id=165649921, authorized=True,
                 session_path=str(sess))

    async def fake_check(api_id, api_hash, session_string):
        return {"valid": True, "user": {"id": 165649921}, "dialogs": 3}

    monkeypatch.setattr(L, "check_session", fake_check)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "1")
    asyncio.run(L._test_session(store, 111, "hash"))
    row = store.get(1)
    assert bool(row["authorized"]) is True      # record not disturbed
    assert row["last_verified_at"] is not None


def test_choice3_account_two_also_works(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite3"
    store = L.AccountStore(db)
    for i, (phone, uid) in enumerate((("+989394430100", 165649921),
                                      ("+5511991966422", 7768075024)), 1):
        sess = tmp_path / f"sess{i}.session"
        sess.write_text("S" + str(i), encoding="utf-8")
        store.upsert(phone, telegram_user_id=uid, authorized=True, session_path=str(sess))

    async def fake_check(api_id, api_hash, session_string):
        return {"valid": True, "user": {"id": 0}, "dialogs": 2}

    monkeypatch.setattr(L, "check_session", fake_check)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")
    asyncio.run(L._test_session(store, 111, "hash"))
    assert bool(store.get(2)["authorized"]) is True
    assert bool(store.get(1)["authorized"]) is True   # other record untouched
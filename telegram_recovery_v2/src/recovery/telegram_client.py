"""Telegram client factory for the recovery v2 engine.

One connected TelegramClient per account. The engine owns the loop; callers
use `async with ClientPool(cfg):` and index by label ("A" / "B").
"""

from __future__ import annotations

from typing import Any

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession


def _proxy_from_url(url: str | None):
    if not url:
        return None
    import socks  # provided via PySocks if a proxy is configured

    if url.startswith("socks5://"):
        rest = url[len("socks5://") :]
    elif url.startswith("socks4://"):
        rest = url[len("socks4://") :]
    else:
        raise ValueError(f"Unsupported proxy scheme: {url}")
    hostport = rest.split("@")[-1]
    host, _, port = hostport.partition(":")
    return (socks.SOCKS5 if url.startswith("socks5") else socks.SOCKS4, host, int(port or 1080))


class ClientPool:
    """Owns one connected client per configured account."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._clients: dict[str, TelegramClient] = {}
        self._tg_ids: dict[str, int] = {}

    async def __aenter__(self) -> "ClientPool":
        for acc in (self.cfg.account_a, self.cfg.account_b):
            client = TelegramClient(
                StringSession(acc.session),
                acc.api_id,
                acc.api_hash,
                device_model="RecoveryV2",
                app_version="2.0",
            )
            client._proxy = _proxy_from_url(self.cfg.proxy) or client._proxy
            await client.connect()
            me = await client.get_me()
            if me is None:
                raise RuntimeError(f"Account {acc.label} ({acc.phone}) session is not authorized")
            self._clients[acc.label] = client
            self._tg_ids[acc.label] = me.id
        return self

    async def __aexit__(self, *exc) -> None:
        for c in self._clients.values():
            try:
                await c.disconnect()
            except Exception:
                pass
        self._clients.clear()

    def client(self, label: str) -> TelegramClient:
        return self._clients[label]

    def tg_id(self, label: str) -> int:
        return self._tg_ids[label]

    def label_for_tg_id(self, tg_id: int) -> str | None:
        for label, ident in self._tg_ids.items():
            if ident == tg_id:
                return label
        return None

    async def resolve_peer(self, label: str, identifier: Any):
        """Resolve a peer for the given account (id/@username/phone)."""
        return await self.client(label).get_input_entity(identifier)

    @staticmethod
    async def call_with_flood_wait(client: TelegramClient, request, **kwargs):
        """Execute a pre-built request object, waiting out FloodWait errors."""
        while True:
            try:
                return await client(request)
            except FloodWaitError as e:
                import asyncio

                await asyncio.sleep(e.seconds + 1)

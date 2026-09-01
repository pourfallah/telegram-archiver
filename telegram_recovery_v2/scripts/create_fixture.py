#!/usr/bin/env python3
"""Create the real A<->B recovery fixture.

**ROLE / NON-GOAL — read this first.** This script is the **SOURCE-FIXTURE
BUILDER** only. It creates the conversation *in account A* by sending ordinary
current-time messages with `send_message` / `send_file`. It is NOT part of the
recovery/history-import path and must never be confused with it. The recovery
import (which reconstructs A's real history into B's copy) is the separate
`ImportEngine` flow that exclusively calls
`checkHistoryImport -> checkHistoryImportPeer -> initHistoryImport ->
uploadImportedMedia -> startHistoryImport` (grep `scripts/` vs `src/`).

Everything this script produces is **current-time** (rule #19): creating true
historical-dated messages is not possible via the public API. That is why the
timestamp truth test (`scripts/minimal_import_tests.py timestamp`) re-uses a
real old source message rather than expecting this builder to fabricate dates.

Sends a set of distinct message types (text, formatted text, emoji, photo,
photo+caption, webp, gif, video, audio, voice, document, reply, reactions by A
and (optionally) B, two-photo album, album-with-caption, forwarded audio) into
the recovery peer as SOURCE ACCOUNT A. Assumes RECOVERY_* credentials and an
existing A<->B conversation are configured.

Writes ``scripts/fixtures/fixture_manifest.json`` listing every sent message
with a stable ``RECOVERY_V2_`` marker so later steps can find it. Capabilities
that cannot be produced here (e.g. a true Telegram sticker, custom emoji,
forward-from-a-channel) are recorded as ``NOT_AVAILABLE`` — never silently
skipped.

Run from the repository root:
    python3 scripts/create_fixture.py            # A reacts
    python3 scripts/create_fixture.py --react-b  # A AND B react (B needs a session)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.tl import functions as f
from telethon.tl import types as t
from telethon.utils import get_input_peer

from recovery.config import RecoveryConfig, load_dotenv

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "fixtures" / "media"
MANIFEST = ROOT / "fixtures" / "fixture_manifest.json"
MARK = "RECOVERY_V2_"


def stamp(s: str) -> str:
    return f"{MARK}{s}"


async def main(react_b: bool) -> int:
    load_dotenv()
    cfg = RecoveryConfig.from_env()
    if not (cfg.api_id_a and cfg.api_hash_a and cfg.session_a()):
        sys.exit("SOURCE A not configured (RECOVERY_API_ID_A/HASH_A/SESSION_A_*)."
                 " Run `recovery-v2 accounts login --actor a` first.")
    if react_b and not (cfg.api_id_b and cfg.api_hash_b and cfg.session_b()):
        sys.exit("--react-b needs TARGET B configured")

    from telethon.sessions import StringSession
    from recovery.telegram_client import default_connect

    peer_str = cfg.peer or cfg.phone_b
    if not peer_str:
        sys.exit("Set RECOVERY_PEER (the A<->B chat)")

    client = TelegramClient(StringSession(cfg.session_a()), cfg.api_id_a,
                            cfg.api_hash_a, device_model="Recovery V2 fixture")
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Source A session is not authorized")
    peer = await client.get_input_entity(peer_str)

    sent: dict[str, dict] = {}
    ok = lambda name, mid, extra=None: sent.update({name: {"status": "SENT", "marker": stamp(name), "message_id": mid, **({} if extra is None else extra)}})
    na = lambda name, note="": sent.update({name: {"status": "NOT_AVAILABLE", "note": note}})
    fail = lambda name, err: sent.update({name: {"status": "FAILED", "error": str(err)[:200]}})

    async def send(name, **kw):
        try:
            m = await client.send_message(peer, stamp(name), **kw)
            ok(name, m.id)
        except Exception as e:  # noqa: BLE001
            fail(name, e)

    def photo_file(n):
        p = MEDIA / n
        return str(p) if p.exists() else None

    # 1-2 text + formatted text + emoji
    await send("text", message=stamp("TEXT_FIXTURE"))
    await send("formatted", message=stamp("FORMATTED**bold** _italic_ `code`"), parse_mode="md")
    await send("emoji", message=f"{stamp('EMOJI')} 😀🎉")

    # 4 custom emoji: requires a real custom emoji doc id; N/A unless provided
    na("custom_emoji", "requires a real custom-emoji document_id; not produced automatically")

    # 5-8 photos / webp
    await send("photo", file=photo_file("photo.jpg"))
    await send("photo_caption", file=photo_file("photo-caption.jpg"),
               message=stamp("PHOTO_CAPTION"))
    await send("photo_png", file=photo_file("photo.png"))
    await send("webp_file", file=photo_file("sticker.webp"))

    # 9 gif/animation, 10 video, 11 audio, 12 voice, 13 document
    await send("gif", file=photo_file("animation.gif"))
    await send("video", file=photo_file("video.mp4"))
    await send("audio", file=photo_file("audio.mp3"))
    await send("voice", file=photo_file("voice.mp3"), voice_note=True)
    await send("document", file=photo_file("document.pdf"))

    # 14 reply
    parent = await client.send_message(peer, stamp("REPLY_PARENT"))
    try:
        child = await client.send_message(peer, stamp("REPLY_CHILD"), reply_to=parent.id)
        ok("reply_child", child.id, {"reply_to_message_id": parent.id})
        ok("reply_parent", parent.id)
    except Exception as e:  # noqa: BLE001
        fail("reply_child", e)

    # 15 reaction by A on the photo_caption message
    try:
        target = sent["photo_caption"].get("message_id")
        await client(f.messages.SendReactionRequest(peer, msg_id=target, big=False,
                                                    add_to_recent=True,
                                                    reaction=[t.ReactionEmoji(emoticon="👍")]))
        sent["reaction_a"] = {"status": "SENT", "target_marker": stamp("PHOTO_CAPTION"), "target_message_id": target, "reaction": "👍"}
    except Exception as e:  # noqa: BLE001
        fail("reaction_a", e)

    # 16 reaction by B (optional)
    if react_b:
        bclient = TelegramClient(StringSession(cfg.session_b()), cfg.api_id_b,
                                 cfg.api_hash_b, device_model="Recovery V2 fixture")
        await bclient.connect()
        try:
            await bclient(f.messages.SendReactionRequest(peer, msg_id=target, big=False,
                                                         add_to_recent=True,
                                                         reaction=[t.ReactionEmoji(emoticon="❤️")]))
            sent["reaction_b"] = {"status": "SENT", "target_message_id": target, "reaction": "❤️"}
        except Exception as e:  # noqa: BLE001
            fail("reaction_b", e)
        finally:
            await bclient.disconnect()

    # 17 two-photo album; 18 album item with caption
    a1, a2 = photo_file("photo.png"), photo_file("photo.jpg")
    if a1 and a2:
        try:
            al = await client.send_file(peer, [a1, a2], album=True,
                                        caption=stamp("ALBUM_ITEM_CAPTION"))
            ok("album", al[0].id, {"album_ids": [m.id for m in al]})
        except Exception as e:  # noqa: BLE001
            fail("album", e)
    else:
        na("album", "missing media files")
    from telethon.tl.functions.messages import SendMediaRequest  # noqa
    # 19 forwarded channel audio: only when a source is provided
    na("forwarded_audio", "set RECOVERY_FORWARD_CHANNEL / RECOVERY_FORWARD_MSG to test")

    # 20 media without caption
    await send("media_no_caption", file=photo_file("photo.jpg"))

    # 21 text adjacent to media (media already has a following text marker)
    await send("media_adjacent_text", file=photo_file("photo.png"),
               message=stamp("MEDIA_ADJACENT_TEXT"))

    MANIFEST.write_text(json.dumps({
        "generated_at": "now", "peer": peer_str, "marker_prefix": MARK,
        "fixtures": sent,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(sent, ensure_ascii=False, indent=2))
    await client.disconnect()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--react-b", action="store_true")
    a = p.parse_args()
    raise SystemExit(__import__("asyncio").run(main(a.react_b)))
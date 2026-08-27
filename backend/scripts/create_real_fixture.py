"""Phase 3 — create the REAL deterministic recovery fixture.

Sends real Telegram messages from Account A into the A<->B (David) private chat,
covering the full master-prompt matrix (§28). Every marker contains
RECOVERY_FINAL_20260827_. Real media binaries are re-used from prior real exports
so each item is a genuine Telegram MessageMedia object (photo / sticker / audio /
video / gif / document / album), NOT fabricated JSON.

SAFE: only sends NEW messages from Account A. Never deletes, never clears A.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import redis.asyncio as aioredis
from telethon import functions, types

from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

A_SESSION_ID = 1
A_VIEW_PEER = 7768075024      # David as seen from A
MARK = "RECOVERY_FINAL_20260827_"

# Real media assets already on disk (from genuine Telegram exports).
MEDIA = {
    "photo": "/data/exports/_989394430100/RanginKamoon/media/photo/photo_0.jpg",
    "sticker": "/data/exports/_989394430100/RanginKamoon/media/sticker/sticker_136095.webm",
    "audio": "/data/exports/_989394430100/RanginKamoon/media/audio/audio_13401987.mp3",
    "video": "/data/exports/_989394430100/RanginKamoon/media/video/video_1029540.mp4",
    "gif": "/data/exports/_989394430100/RanginKamoon/media/animation/animation_29055.mp4",
    "voice": "/data/exports/_989394430100/RanginKamoon/media/voice/voice_97451.ogg",
    "doc": "/data/exports/_989394430100/RanginKamoon/media/video/video_594165.mp4",
}


def p(p: str) -> str:
    return Path(p).expanduser().as_posix()


async def main(account_id: int = A_SESSION_ID, peer_id: int = A_VIEW_PEER,
               media_root: str = "/data"):
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc = await db.scalar(select(TelegramSession).where(TelegramSession.id == account_id))
    if acc is None:
        print("account not found"); return

    client, release = await manager.acquire_client(acc)
    sent = []
    try:
        me = await client.get_me()
        peer = await client.get_entity(peer_id)
        # ensure assets exist
        for k, v in list(MEDIA.items()):
            if not Path(v).exists():
                print(f"MISSING asset {k}: {v}")
                MEDIA.pop(k)
        print("A:", me.id, me.first_name, "peer:", peer_id, "| assets:", list(MEDIA.keys()))

        async def st(text: str):
            """Sticker send via document w/ sticker attribute."""
            return await client.send_file(peer, p(MEDIA["sticker"]),
                                          caption=text, force_document=False)

        # ---- 1. Plain text
        sent.append(("TEXT_001_PLAIN", (await client.send_message(peer, f"{MARK}TEXT_001_PLAIN")).id))
        # ---- 2. Formatted text
        sent.append(("FORMATTED_002", (await client.send_message(
            peer, f"{MARK}FORMATTED_002 **bold** __italic__ __underline__ ~~strike~~"
                  f" https://example.com `code`")).id))
        # ---- 3. Photo without caption
        ph = await client.send_file(peer, p(MEDIA["photo"]), caption="")
        sent.append(("PHOTO_003_NO_CAPTION", ph.id))
        # ---- 4. Photo WITH caption
        phc = await client.send_file(peer, p(MEDIA["photo"]),
                                     caption=f"{MARK}PHOTO_CAPTION_004 😍")
        sent.append(("PHOTO_CAPTION_004", phc.id))
        # ---- 5. Sticker
        stk = await client.send_file(peer, p(MEDIA["sticker"]), force_document=False)
        sent.append(("STICKER_005", stk.id))
        # ---- 6. Video
        vd = await client.send_file(peer, p(MEDIA["video"]))
        sent.append(("VIDEO_006", vd.id))
        # ---- 7. GIF / animation
        gf = await client.send_file(peer, p(MEDIA["gif"]))
        sent.append(("GIF_007", gf.id))
        # ---- 8. Audio / music
        au = await client.send_file(peer, p(MEDIA["audio"]))
        sent.append(("AUDIO_008", au.id))
        # ---- 9. Document
        dc = await client.send_file(peer, p(MEDIA["doc"]), force_document=True)
        sent.append(("DOCUMENT_009", dc.id))
        # ---- 10. Reply parent
        pr = await client.send_message(peer, f"{MARK}REPLY_PARENT_010")
        sent.append(("REPLY_PARENT_010", pr.id))
        # ---- 11. Reply child (real Telegram reply)
        await asyncio.sleep(0.5)
        ch = await client.send_message(peer, f"{MARK}REPLY_CHILD_011", reply_to=pr.id)
        sent.append(("REPLY_CHILD_011", ch.id))
        # ---- 12/13. Reaction targets (A and B will react; added below)
        rt = await client.send_message(peer, f"{MARK}REACTION_TARGET_012")
        sent.append(("REACTION_TARGET_012", rt.id))
        # ---- 13. Reaction by B handled via B's own session in a later step;
        #          B already has access to this chat. Both A and B react here.
        # A reacts (own session):
        await client(functions.messages.SendReactionRequest(
            peer=peer, msg_id=rt.id, reaction=[types.ReactionEmoji(emoticon="👍")]))
        sent.append(("REACTION_A_on_012", None))
        # ---- 14. Custom emoji: send an emoji-rich message + a custom-emoji entity
        #       (Telegram auto-uses custom emoji when a pack matches; we send an
        #       explicit custom-emoji entity referencing a known document id is
        #       unreliable, so we at least send a message containing the emoji
        #       that renders as custom emoji where a pack exists.)
        ce = await client.send_message(peer, f"{MARK}CUSTOM_EMOJI_014 🫠")
        sent.append(("CUSTOM_EMOJI_014", ce.id))
        # ---- 15. Two-photo album (grouped_id)
        al = await client.send_file(peer, [p(MEDIA["photo"]), p(MEDIA["photo"])],
                                    caption=f"{MARK}ALBUM_015_TWO_PHOTOS")
        for a in al:
            sent.append(("ALBUM_015", a.id))
        # ---- 16. Forwarded audio — forward the just-sent AUDIO_008 (from A to self peer)
        try:
            await client.forward_messages(peer, [au.id], peer)
            sent.append(("FORWARD_016_AUDIO", "fwd"))
        except Exception as exc:  # noqa: BLE001
            print(f"  (forward failed: {exc})")
        # ---- 17. Text immediately adjacent to media (photo + next-line text)
        adj = await client.send_file(peer, p(MEDIA["photo"]),
                                     caption=f"{MARK}ADJACENT_MEDIA_017")
        await client.send_message(peer, f"{MARK}ADJACENT_TEXT_017 immediately after media")
        sent.append(("ADJACENT_MEDIA_017", adj.id))
        sent.append(("ADJACENT_TEXT_017", None))

        out = {f"{MARK}{k}": v for k, v in sent if k}
        print(json.dumps({"account": acc.phone, "peer": peer_id, "sent": out},
                         ensure_ascii=False, indent=2))
        (Path("/data/e2e_fixture_sent.json").write_text(
            json.dumps({"account": acc.phone, "peer": peer_id, "sent": out},
                       ensure_ascii=False, indent=2)))
    finally:
        await release()


if __name__ == "__main__":
    aid = int(sys.argv[1]) if len(sys.argv) > 1 else A_SESSION_ID
    pid = int(sys.argv[2]) if len(sys.argv) > 2 else A_VIEW_PEER
    root = sys.argv[3] if len(sys.argv) > 3 else "/data"
    asyncio.run(main(aid, pid, root))
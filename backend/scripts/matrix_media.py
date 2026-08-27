import asyncio
import io
from telethon import functions, types
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager
from app.config import get_settings
import redis.asyncio as aioredis

JPEG = bytes.fromhex(
    'ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707'
    '070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c'
    '1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100'
    'ffc4001f0000010501010101010100000000000000000102030405060708090a0bff'
    'c400b5100002010303020403050504040000017d0102030004110512213141061351'
    '6107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728'
    '292a3435363738393a434445464748494a535455565758595a636465666768696a73'
    '74757677787a82838485878889929091939495969798999aa2a3a4a5a7a8a9b2b4b5'
    'b7b8b9bac2c3c4c5c7c8c9cad2d3d4d5d7d8d9dae1e2e4e5e7e8e9eaf1f2f4f5f7f8'
    'f9faffda0008010100003f00fbfa28a2803fffd9')


async def one(cb, peer_b, tag: str, body_lines: list[str], fname: str):
    before = {m.id for m in await cb.get_messages(peer_b, limit=30)}
    body = "\n".join(body_lines) + "\n"
    fh = await cb.upload_file(body.encode())
    init = await cb(functions.messages.InitHistoryImportRequest(
        peer=peer_b, file=fh, media_count=1))
    jf = await cb.upload_file(io.BytesIO(JPEG))
    res = await cb(functions.messages.UploadImportedMediaRequest(
        peer=peer_b, import_id=getattr(init, 'id'), file_name=fname,
        media=types.InputMediaUploadedPhoto(file=jf)))
    tok = type(res).__name__
    await cb(functions.messages.StartHistoryImportRequest(
        peer=peer_b, import_id=getattr(init, 'id')))
    for _ in range(6):
        await asyncio.sleep(20)
        msgs = [m for m in await cb.get_messages(peer_b, limit=8)
                if m.id not in before]
        if msgs:
            out = []
            for m in sorted(msgs, key=lambda x: x.id):
                out.append((m.id, (m.message or '')[:34],
                            type(m.media).__name__ if m.media else None))
            print(f"--- {tag} | token={tok}")
            for o in out:
                print("   ", o)
            return
        # keep waiting
    print(f"--- {tag} | token={tok} | NEVER MATERIALIZED")


async def t():
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == 3))
    cb, rb = await manager.acquire_client(acc_b)
    try:
        peer_b = await cb.get_entity(165649921)
        # A: proven legacy format — marker alone
        await one(cb, peer_b, "A_legacy_alone",
                  ["[10/10/2024, 10:00:00] Alice: <attached: pa.jpg>",
                   "[10/10/2024, 10:01:00] Alice: after_a"], "pa.jpg")
        # B: legacy marker + caption continuation
        await one(cb, peer_b, "B_legacy_caption",
                  ["[11/11/2024, 11:00:00] Alice: <attached: pb.jpg>\nMY_CAPTION_B",
                   "[11/11/2024, 11:01:00] Alice: after_b"], "pb.jpg")
        # C: bracket NODASH + (file attached)
        await one(cb, peer_b, "C_bracket_attached",
                  ["[12/12/2024, 12:00:00] Alice: pc.jpg (file attached)",
                   "[12/12/2024, 12:01:00] Alice: after_c"], "pc.jpg")
    finally:
        await rb()


asyncio.run(t())

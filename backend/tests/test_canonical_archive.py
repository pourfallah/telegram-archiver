"""Canonical archive builder tests."""
import json
from pathlib import Path

from app.services import canonical_archive


def _make_export(tmp_path: Path) -> Path:
    out = tmp_path / "export"
    out.mkdir(parents=True)
    media = out / "media" / "photo"
    media.mkdir(parents=True)
    rows = []
    for i in range(1, 7):
        m = {
            "id": i,
            "grouped_id": 77 if i in (2, 3) else None,
            "date": f"2024-01-{i:02d}T10:0{i}:00+00:00",
            "edited": None,
            "sender": {"id": 1 if i % 2 else 2, "name": "Alice" if i % 2 else "Bob", "username": None},
            "text": f"msg {i}",
            "entities": [],
            "reply_to": (i - 1) if i > 1 else None,
            "forwarded_from": None,
            "reactions": {"\u2764": 1} if i == 4 else None,
            "views": None,
            "forwards": None,
            "media": [{"type": "photo", "filename": f"p{i}.png", "file_path": f"media/photo/p{i}.png", "size_bytes": 3}] if i % 3 == 0 else [],
        }
        rows.append(m)
        if i % 3 == 0:
            (media / f"p{i}.png").write_bytes(b"123")
    (out / "messages.json").write_text(json.dumps({"schema_version": 1, "chat": {"id": -100, "title": "T", "type": "private"}, "messages": rows}), encoding="utf-8")
    return out


def test_canonical_archive_lossless(tmp_path):
    export = _make_export(tmp_path)
    dst = tmp_path / "archive"
    stats = canonical_archive.build_canonical_archive(export, dst, {"title": "T", "type": "private"})

    assert stats["messages"] == 6
    assert stats["files"] == 2  # i in (3,6) -> media
    assert stats["users"]
    assert (dst / "manifest.json").exists()
    assert (dst / "chat.json").exists()
    assert (dst / "participants.json").exists()
    assert (dst / "messages" / "messages.ndjson").exists()

    lines = (dst / "messages" / "messages.ndjson").read_text().splitlines()
    msgs = [json.loads(ln) for ln in lines]
    assert msgs[0]["date"] < msgs[-1]["date"]  # oldest-first
    grouping = {m["id"]: m["grouped_id"] for m in msgs}
    assert grouping[2] == grouping[3] == 77  # album preserved
    assert grouping[1] is None

    assert (dst / "media" / "photo" / "p3.png").exists()
    checks = json.loads((dst / "checksums.json").read_text())
    assert any("p3.png" in k for k in checks)


def test_canonical_archive_from_jsonl_partial(tmp_path):
    export = tmp_path / "exp"
    media = export / "media" / "document"
    media.mkdir(parents=True)
    rows = [{"id": 2, "date": "2024-02-02T00:00:00+00:00", "sender": {"id": 7, "name": "X"},
             "text": "hi", "entities": [], "media": []}]
    (export / "messages.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    dst = tmp_path / "arch2"
    stats = canonical_archive.build_canonical_archive(export, dst, {})
    assert stats["messages"] == 1
    assert (dst / "messages" / "messages.ndjson").exists()

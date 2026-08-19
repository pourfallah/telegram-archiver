"""Unit tests for the WhatsApp converter, test builder and import assistant."""
import json

from app.services import import_assistant, test_builder
from app.services.converter import _sender_names, build_whatsapp_package


def _sample_messages():
    return [
        {"id": 1, "date": "2024-01-01T09:00:00+00:00", "edited": None,
         "sender": {"id": 1, "name": "Alice", "username": "alice"},
         "text": "Hello", "entities": [], "reply_to": None, "forwarded_from": None,
         "reactions": None, "views": None, "forwards": None, "media": []},
        {"id": 2, "date": "2024-01-02T10:30:00+00:00", "edited": None,
         "sender": {"id": 2, "name": "Bob", "username": "bob"},
         "text": "", "entities": [], "reply_to": None, "forwarded_from": None,
         "reactions": None, "views": None, "forwards": None,
         "media": [{"type": "photo", "filename": "photo_1.jpg", "file_path": "media/photo/photo_1.jpg"}]},
        {"id": 3, "date": "2024-01-02T10:31:00+00:00", "edited": None,
         "sender": {"id": 2, "name": "Bob", "username": "bob"},
         "text": "caption", "entities": [], "reply_to": None, "forwarded_from": None,
         "reactions": None, "views": None, "forwards": None, "media": []},
    ]


def test_sender_mapping_never_merges(tmp_path):
    msgs = _sample_messages()
    mapping = _sender_names(msgs)
    assert mapping[1] == "Alice"
    assert mapping[2] == "Bob"
    # Distinct sender ids always yield distinct names.
    assert len({k: v for k, v in mapping.items() if k is not None}) == len(mapping)


def test_converter_line_format_and_attachments(tmp_path):
    export_dir = tmp_path / "export"
    media_dir = export_dir / "media" / "photo"
    media_dir.mkdir(parents=True)
    (media_dir / "photo_1.jpg").write_bytes(b"jpgbytes")
    msgs = _sample_messages()
    for m in msgs:
        if m["media"]:
            m["media"][0]["file_path"] = "media/photo/photo_1.jpg"
    (export_dir / "messages.json").write_text(
        json.dumps({"messages": msgs}, ensure_ascii=False), encoding="utf-8"
    )

    out = tmp_path / "package"
    manifest = build_whatsapp_package(export_dir, out)

    assert manifest["messages"] == 3
    assert manifest["media"] == 1
    assert (out / "media" / "photo_1.jpg").exists()

    chat = (out / "_chat.txt").read_text(encoding="utf-8")
    assert "01/01/2024, 09:00 - Alice: Hello" in chat
    assert "02/01/2024, 10:30 - Bob: <Attached: photo_1.jpg>" in chat
    assert "02/01/2024, 10:31 - Bob: caption" in chat


def test_test_builder_produces_valid_package(tmp_path):
    out = tmp_path / "pkg"
    manifest = test_builder.build_test_package(50, out)
    assert manifest["messages"] == 50
    assert manifest["media"] > 0
    assert (out / "_chat.txt").exists()
    assert (out / "manifest.json").exists()
    assert len(list((out / "media").glob("*"))) == manifest["media"]


def test_import_assistant_valid(tmp_path):
    out = tmp_path / "pkg"
    test_builder.build_test_package(100, out)
    report = import_assistant.validate_package(out)
    assert report["validation_status"] == "valid"
    assert report["stats"]["messages"] == 100
    assert report["stats"]["media"] > 0
    instructions = import_assistant.generate_instructions(out)
    assert any("Import from WhatsApp" in step["detail"] for step in instructions)


def test_import_assistant_invalid_missing_files(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report = import_assistant.validate_package(empty)
    assert report["validation_status"] == "invalid"
    assert any("missing _chat.txt" in i for i in report["issues"])

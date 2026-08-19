"""Export writers — the on-disk artifacts of an export.

Working file (streamed during the run, always crash-safe):
    messages.jsonl   one JSON message object per line

Final artifacts (assembled at finalize from the Postgres ledger, so a resumed
run produces identical, complete outputs):
    messages.json    canonical archive (schema v1)
    database.sqlite  portable SQLite archive
    index.html + pages/  browser-browsable HTML export
"""
from __future__ import annotations

import html
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JSON_SCHEMA_VERSION = 1
HTML_PAGE_SIZE = 5000
BUILD_BATCH = 2000

SENDER_COLORS = [
    "#e63946", "#457b9d", "#2a9d8f", "#e9c46a", "#f4a261",
    "#9b5de5", "#00bbf9", "#f15bb5", "#90be6d", "#fe7f2d",
]


class JsonLineWriter:
    """Append-only NDJSON writer. Open with the count of previously written
    lines so a resumed run keeps numbering correctly."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        self.count = 0

    def open(self, resume_count: int = 0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        self.count = resume_count

    def write_batch(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        assert self._file is not None, "writer not open"
        for row in rows:
            self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.count += 1

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None


def assemble_json_archive(
    lines_path: Path,
    out_path: Path,
    chat_header: dict[str, Any],
    stats: dict[str, Any],
) -> None:
    """Wrap the NDJSON workfile into the canonical messages.json document.

    The NDJSON workfile is written newest-first as the export streams; the
    canonical archive presents messages oldest-first (ascending id), matching
    Telegram's own export ordering. Reversal happens only here at finalize.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as out:
        out.write("{\n")
        out.write(f'"schema_version": {JSON_SCHEMA_VERSION},\n')
        out.write(f'"exported_at": "{datetime.now(UTC).isoformat()}",\n')
        out.write(f'"chat": {json.dumps(chat_header, ensure_ascii=False)},\n')
        out.write(f'"stats": {json.dumps(stats, ensure_ascii=False)},\n')
    with lines_path.open("r", encoding="utf-8") as src:
        lines = [ln.strip() for ln in src if ln.strip()]
    lines.reverse()
    with out_path.open("w", encoding="utf-8") as out:
        out.write("{\n")
        out.write(f'"schema_version": {JSON_SCHEMA_VERSION},\n')
        out.write(f'"exported_at": "{datetime.now(UTC).isoformat()}",\n')
        out.write(f'"chat": {json.dumps(chat_header, ensure_ascii=False)},\n')
        out.write(f'"stats": {json.dumps(stats, ensure_ascii=False)},\n')
        out.write('"messages": [')
        for i, line in enumerate(lines):
            if i:
                out.write(",\n")
            out.write(line)
        out.write("\n]\n}\n")


class SqliteArchiveBuilder:
    """Builds database.sqlite from Postgres ledger batches."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY,
        date TEXT,
        edit_date TEXT,
        sender_id INTEGER,
        sender_name TEXT,
        sender_username TEXT,
        text TEXT,
        entities TEXT,
        reply_to_message_id INTEGER,
        forwarded_from TEXT,
        reactions TEXT,
        views INTEGER,
        forwards INTEGER,
        media TEXT
    );
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        media_type TEXT,
        mime_type TEXT,
        size_bytes INTEGER,
        original_filename TEXT,
        filename TEXT,
        sha256 TEXT,
        file_path TEXT
    );
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """

    def __init__(self, path: Path, chat_header: dict[str, Any]) -> None:
        self.path = path
        self.chat_header = chat_header
        self.messages_written = 0

    def create(self) -> None:
        import sqlite3

        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.executescript(self.SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('chat', ?)",
            (json.dumps(self.chat_header, ensure_ascii=False),),
        )
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '1')")
        conn.commit()
        conn.close()

    def write_batch(self, msgs: list[dict[str, Any]], media_rows: list[dict[str, Any]]) -> None:
        """Write one Postgres-ledger batch."""
        if not msgs:
            return
        import sqlite3

        conn = sqlite3.connect(self.path)
        try:
            conn.executemany(
                """INSERT OR REPLACE INTO messages(
                    message_id, date, edit_date, sender_id, sender_name, sender_username,
                    text, entities, reply_to_message_id, forwarded_from, reactions,
                    views, forwards, media
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        m["message_id"],
                        _iso(m["date"]),
                        _iso(m["edit_date"]),
                        m["sender_id"],
                        m["sender_name"],
                        m["sender_username"],
                        m["text"],
                        json.dumps(m.get("entities") or [], ensure_ascii=False),
                        m["reply_to_message_id"],
                        json.dumps(m["forwarded_from"], ensure_ascii=False) if m["forwarded_from"] else None,
                        json.dumps(m["reactions"], ensure_ascii=False) if m["reactions"] else None,
                        m["views"],
                        m["forwards"],
                        json.dumps(m.get("media") or [], ensure_ascii=False),
                    )
                    for m in msgs
                ],
            )
            if media_rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO media(
                        message_id, media_type, mime_type, size_bytes,
                        original_filename, filename, sha256, file_path
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    [
                        (
                            r["message_id"], r["media_type"], r["mime_type"], r["size_bytes"],
                            r["original_filename"], r["filename"], r["sha256"], r["file_path"],
                        )
                        for r in media_rows
                    ],
                )
            conn.commit()
        finally:
            conn.close()

    def finalize(self, stats: dict[str, Any]) -> None:
        import sqlite3

        conn = sqlite3.connect(self.path)
        try:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('stats', ?)",
                         (json.dumps(stats, ensure_ascii=False),))
            conn.commit()
        finally:
            conn.close()


class HtmlExportBuilder:
    """Builds index.html + pages/ from Postgres ledger batches."""

    def __init__(self, out_dir: Path, chat_header: dict[str, Any], page_size: int = HTML_PAGE_SIZE) -> None:
        self.out_dir = out_dir
        self.chat_header = chat_header
        self.page_size = page_size
        self._buffer: list[str] = []
        self._pages: list[Path] = []
        self._sender_colors: dict[int, str] = {}
        self._next_color = 0

    def create(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "pages").mkdir(parents=True, exist_ok=True)

    def _color_for(self, sender_id: int | None) -> str:
        if sender_id is None:
            return "#888888"
        if sender_id not in self._sender_colors:
            self._sender_colors[sender_id] = SENDER_COLORS[self._next_color % len(SENDER_COLORS)]
            self._next_color += 1
        return self._sender_colors[sender_id]

    def write_batch(self, msgs: list[dict[str, Any]], media_by_msg: dict[int, list[dict[str, Any]]]) -> None:
        for m in msgs:
            self._buffer.append(self._render_row(m, media_by_msg.get(m["message_id"], [])))
            if len(self._buffer) >= self.page_size:
                self._flush_page()

    def _render_row(self, m: dict[str, Any], media_rows: list[dict[str, Any]]) -> str:
        sender_name = html.escape(m.get("sender_name") or m.get("sender_username") or "Unknown")
        color = self._color_for(m.get("sender_id"))
        when = html.escape(str(m.get("date") or ""))
        text = html.escape(m.get("text") or "").replace("\n", "<br>")
        media_links = [
            f'<li class="media"><a href="../media/{html.escape(r.get("media_type", "media"))}/'
            f'{html.escape(r.get("filename") or r.get("original_filename") or "file")}" '
            f'title="{html.escape(r.get("mime_type") or "")}">'
            f'[{html.escape(r.get("media_type", "media"))}] '
            f'{html.escape(r.get("original_filename") or r.get("filename") or "file")}</a></li>'
            for r in media_rows
        ]
        media_html = f'<ul class="media-list">{"".join(media_links)}</ul>' if media_links else ""
        reply = f'<span class="reply">↩︎ #{m.get("reply_to_message_id")}</span>' if m.get("reply_to_message_id") else ""
        edited = f'<span class="edited">(edited {html.escape(str(m.get("edit_date")))})</span>' if m.get("edit_date") else ""
        views = f'<span class="views">👁 {m.get("views")}</span>' if m.get("views") else ""
        return (
            f'<div class="message" id="msg-{m.get("message_id")}">'
            f'<span class="meta">#{m.get("message_id")} {when} {edited} {views}</span><br>'
            f'<span class="sender" style="color:{color}">{sender_name}:</span> '
            f'<span class="text">{text}</span> {reply}{media_html}'
            f"</div>"
        )

    def _flush_page(self) -> None:
        page_no = len(self._pages) + 1
        path = self.out_dir / "pages" / f"page-{page_no:05d}.html"
        prev_link = f'<a href="page-{page_no - 1:05d}.html">‹ prev</a>' if page_no > 1 else ""
        next_link = '<a href="../index.html">index</a>'
        body = "\n".join(self._buffer)
        path.write_text(
            f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(str(self.chat_header.get('title', 'chat')))} — page {page_no}</title>"
            f"<style>body{{font-family:system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;}}"
            f".message{{border-bottom:1px solid #eee;padding:.8rem 0;}}"
            f".meta{{color:#999;font-size:.8rem;}}.reply{{color:#888;}}</style></head><body>"
            f"<h1>{html.escape(str(self.chat_header.get('title', 'chat')))} — page {page_no}</h1>"
            f"<p>{prev_link} · {next_link}</p>{body}<p>{prev_link} · {next_link}</p></body></html>",
            encoding="utf-8",
        )
        self._pages.append(path)
        self._buffer = []

    def finalize(self, stats: dict[str, Any]) -> None:
        if self._buffer:
            self._flush_page()
        links = "".join(
            f'<li><a href="pages/page-{i + 1:05d}.html">page {i + 1}</a></li>'
            for i in range(len(self._pages))
        )
        chat = self.chat_header
        index = (
            f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(str(chat.get('title', 'chat')))} — export</title>"
            f"<style>body{{font-family:system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;}}</style>"
            f"</head><body>"
            f"<h1>Telegram export: {html.escape(str(chat.get('title', 'chat')))}</h1>"
            f"<p>Account: {html.escape(str(chat.get('account', {}).get('phone', '?')))} · "
            f"Chat id: {chat.get('id')} · Type: {chat.get('type')}</p>"
            f"<p>Messages: {stats.get('messages')} · Media: {stats.get('media')} · "
            f"Range: {html.escape(str(stats.get('first_date', '?')))} → {html.escape(str(stats.get('last_date', '?')))}</p>"
            f"<p>Generated: {datetime.now(UTC).isoformat()}</p>"
            f"<h2>Pages ({len(self._pages)})</h2><ul>{links}</ul>"
            f"<h2>Files</h2><ul><li><a href='messages.json'>messages.json</a></li>"
            f"<li><a href='database.sqlite'>database.sqlite</a></li></ul>"
            f"</body></html>"
        )
        (self.out_dir / "index.html").write_text(index, encoding="utf-8")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)

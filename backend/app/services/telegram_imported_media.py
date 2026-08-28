"""
Canonical Telegram Imported Media Service

Single production service responsible for ALL media import operations.
Consolidates successful patterns from:
- caption_final_test.py (caption + media in ONE block)
- matrix_media.py (InputMediaUploadedPhoto with file_name)
- minimal_import_test.py (rich attributes: sticker, audio, video, gif)
- import_tasks.py (media trace, extra metadata)

Every caller must use this service. No duplicate media-import implementations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from telethon.tl import functions, types

logger = logging.getLogger(__name__)


@dataclass
class MediaUploadSpec:
    """Specification for a single media upload."""
    source_message_id: int
    filename: str
    media_type: str  # photo, document, sticker, animation, video, audio, voice
    mime_type: str
    file_path: Path
    file_size: int
    sha256: str
    extra: dict  # type-specific: alt/width/height/duration/performer/title
    grouped_id: Optional[int] = None  # for albums


@dataclass
class MediaUploadResult:
    """Result of a single media upload."""
    source_message_id: int
    filename: str
    media_type: str
    input_media_ctor: str  # InputMediaUploadedPhoto / InputMediaUploadedDocument
    returned_ctor: str  # MessageMediaEmpty / MessageMediaPhoto / MessageMediaDocument
    returned_photo_id: Optional[int] = None
    returned_document_id: Optional[int] = None
    error: Optional[str] = None


@dataclass
class MediaImportTrace:
    """Complete trace of media import for auditability."""
    import_id: int
    target_peer_id: int
    uploads: list[MediaUploadResult]
    total_declared: int
    total_uploaded: int
    total_succeeded: int
    total_failed: int


class TelegramImportedMediaService:
    """
    Single canonical service for Telegram history import media operations.
    
    Responsibilities:
    - prepare media (build InputMedia with correct attributes)
    - upload media file to Telegram
    - uploadImportedMedia with correct InputMedia constructor
    - receive MessageMedia token
    - store association trace
    - map source message -> filename -> server token
    - handle grouped_id for albums
    - verify upload results
    """
    
    def __init__(self, client):
        """
        Args:
            client: Connected Telethon TelegramClient
        """
        self.client = client
    
    async def build_input_media(self, spec: MediaUploadSpec) -> types.TypeInputMedia:
        """
        Build the correct InputMedia constructor for uploadImportedMedia.
        
        Based on successful patterns from:
        - minimal_import_test.py (rich attributes for all types)
        - caption_final_test.py (InputMediaUploadedPhoto for photos)
        
        Args:
            spec: MediaUploadSpec with all metadata
            
        Returns:
            InputMediaUploadedPhoto or InputMediaUploadedDocument with attributes
        """
        # Upload file to get InputFile handle
        handle = await self._upload_file(spec)
        
        if spec.media_type == "photo" or (spec.mime_type.startswith("image/") and spec.mime_type != "image/webp"):
            # Photo: use InputMediaUploadedPhoto (successful in caption_final_test.py)
            return types.InputMediaUploadedPhoto(file=handle)
        
        # Document types: use InputMediaUploadedDocument with attributes
        attributes = self._build_document_attributes(spec)
        
        # Always add filename attribute if not present
        if not any(isinstance(a, types.DocumentAttributeFilename) for a in attributes):
            attributes.append(types.DocumentAttributeFilename(file_name=spec.filename))
        
        return types.InputMediaUploadedDocument(
            file=handle,
            mime_type=spec.mime_type,
            attributes=attributes,
        )
    
    async def _upload_file(self, spec: MediaUploadSpec) -> types.TypeInputFile:
        """Upload file to Telegram and return InputFile handle.

        NOTE: client.upload_file is a COROUTINE and MUST be awaited. The prior
        sync `def` returned the unawaited coroutine, which telethon rejected at
        uploadImportedMedia time with "a TLObject was expected but found
        something else". This was a real regression the live E2E exposed.
        """
        handle = await self.client.upload_file(spec.file_path, file_name=spec.filename)
        return handle
    
    def _build_document_attributes(self, spec: MediaUploadSpec) -> list:
        """Build document attributes based on media type and extra metadata.
        
        Mirrors successful patterns from minimal_import_test.py:
        - sticker: DocumentAttributeSticker + DocumentAttributeImageSize
        - animation/gif: DocumentAttributeAnimated
        - video: DocumentAttributeVideo
        - audio: DocumentAttributeAudio
        - voice: DocumentAttributeAudio(voice=True)
        """
        from telethon.tl.types import (
            DocumentAttributeAnimated,
            DocumentAttributeAudio,
            DocumentAttributeImageSize,
            DocumentAttributeSticker,
            DocumentAttributeVideo,
            InputStickerSetEmpty,
        )
        
        attributes = []
        extra = spec.extra or {}
        
        if spec.media_type == "sticker":
            # Sticker: alt emoji + empty stickerset (server may upgrade)
            attributes.append(types.DocumentAttributeSticker(
                alt=str(extra.get("alt", "")),
                stickerset=types.InputStickerSetEmpty()
            ))
            w, h = extra.get("width"), extra.get("height")
            if w and h:
                attributes.append(types.DocumentAttributeImageSize(w=int(w), h=int(h)))
        
        elif spec.media_type in ("animation", "gif"):
            attributes.append(types.DocumentAttributeAnimated())
        
        elif spec.media_type == "video":
            # DocumentAttributeVideo requires INT values for duration/w/h at the
            # wire level — `or None` makes struct.pack fail with
            # "required argument is not an integer" on send (real E2E job 48).
            # Coerce missing values to 0 (client accepts None but the RPC codec
            # does not).
            attributes.append(types.DocumentAttributeVideo(
                duration=int(float(extra.get("duration", 0) or 0) or 0),
                w=int(extra.get("width", 0) or 0),
                h=int(extra.get("height", 0) or 0),
            ))
        
        elif spec.media_type == "audio":
            attributes.append(types.DocumentAttributeAudio(
                duration=int(float(extra.get("duration", 0) or 0)),
                performer=extra.get("performer"),
                title=extra.get("title"),
            ))
        
        elif spec.media_type == "voice":
            attributes.append(types.DocumentAttributeAudio(
                voice=True,
                duration=int(float(extra.get("duration", 0) or 0)),
            ))
        
        return attributes
    
    async def upload_imported_media(
        self,
        peer,
        import_id: int,
        spec: MediaUploadSpec
    ) -> MediaUploadResult:
        """
        Upload a single media file via uploadImportedMedia.
        
        This is the EXACT pattern from caption_final_test.py that succeeded:
        1. Upload file to Telegram (get InputFile)
        2. Build InputMedia (InputMediaUploadedPhoto or InputMediaUploadedDocument)
        3. Call uploadImportedMedia with file_name and media
        
        Args:
            peer: Target peer (InputPeer)
            import_id: Import ID from initHistoryImport
            spec: MediaUploadSpec
            
        Returns:
            MediaUploadResult with token info
        """
        try:
            # Build InputMedia with correct constructor and attributes
            input_media = await self.build_input_media(spec)
            input_ctor = type(input_media).__name__
            
            # Call uploadImportedMedia
            result = await self.client(functions.messages.UploadImportedMediaRequest(
                peer=peer,
                import_id=import_id,
                file_name=spec.filename,
                media=input_media,
            ))
            
            returned_ctor = type(result).__name__ if result else "None"
            photo_id = getattr(getattr(result, "photo", None), "id", None)
            doc_id = getattr(getattr(result, "document", None), "id", None)
            
            logger.info(
                f"Uploaded media {spec.filename}: {returned_ctor} "
                f"photo_id={photo_id} doc_id={doc_id}"
            )
            
            return MediaUploadResult(
                source_message_id=spec.source_message_id,
                filename=spec.filename,
                media_type=spec.media_type,
                input_media_ctor=input_ctor,
                returned_ctor=returned_ctor,
                returned_photo_id=photo_id,
                returned_document_id=doc_id,
            )
            
        except Exception as e:
            logger.error(f"Media upload failed for {spec.filename}: {e}")
            return MediaUploadResult(
                source_message_id=spec.source_message_id,
                filename=spec.filename,
                media_type=spec.media_type,
                input_media_ctor="ERROR",
                returned_ctor="ERROR",
                error=str(e),
            )
    
    async def upload_all_media(
        self,
        peer,
        import_id: int,
        specs: list[MediaUploadSpec]
    ) -> MediaImportTrace:
        """
        Upload all media files for an import.
        
        Args:
            peer: Target peer
            import_id: Import ID from initHistoryImport
            specs: List of MediaUploadSpec (one per media item in import)
            
        Returns:
            MediaImportTrace with complete audit trail
        """
        uploads = []
        succeeded = 0
        failed = 0
        
        for spec in specs:
            result = await self.upload_imported_media(peer, import_id, spec)
            uploads.append(result)
            if result.error:
                failed += 1
            else:
                succeeded += 1
        
        trace = MediaImportTrace(
            import_id=import_id,
            target_peer_id=getattr(peer, "peer_id", getattr(peer, "id", 0)),
            uploads=uploads,
            total_declared=len(specs),
            total_uploaded=len(specs),
            total_succeeded=succeeded,
            total_failed=failed,
        )
        
        return trace
    
    def write_trace(self, trace: MediaImportTrace, output_path: Path) -> None:
        """Write MEDIA_IMPORT_TRACE.json for auditability."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Convert to dict for JSON serialization
        data = {
            "import_id": trace.import_id,
            "target_peer_id": trace.target_peer_id,
            "total_declared": trace.total_declared,
            "total_uploaded": trace.total_uploaded,
            "total_succeeded": trace.total_succeeded,
            "total_failed": trace.total_failed,
            "uploads": [asdict(u) for u in trace.uploads],
        }
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"Media import trace written to {output_path}")


def build_media_specs_from_archive(
    export_dir: Path,
    import_text: str,
    limit: Optional[int] = None
) -> list[MediaUploadSpec]:
    """
    Build MediaUploadSpec list from canonical archive and import file.
    
    This is the SINGLE function that determines what media gets uploaded.
    Must match the import file's <attached: FILENAME> markers exactly.
    
    Args:
        export_dir: Export directory containing archive/media
        import_text: Content of import.txt (for marker matching)
        limit: Optional limit on messages
        
    Returns:
        List of MediaUploadSpec for all media referenced in import file
    """
    import re
    
    # Extract filenames from import file markers
    # ONLY accept <attached: FILENAME> (bracket format family)
    # Reject (file attached) format which is for DD/MM/YY family
    wanted_filenames = []
    for m in re.finditer(r"<attached:\s*([^>]+)>", import_text):
        wanted_filenames.append(m.group(1).strip())
    
    if not wanted_filenames:
        logger.warning("No <attached:> markers found in import file")
        return []
    
    # Load canonical archive messages
    archive_dir = export_dir / "archive"
    media_src = export_dir / "media"
    if archive_dir.exists():
        media_src = archive_dir / "media"
    
    # Load message extra metadata from canonical archive
    media_extra = {}  # filename -> extra dict
    try:
        for ndjson in sorted((archive_dir / "messages").glob("*.ndjson")):
            for ln in ndjson.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                row = json.loads(ln)
                for med in row.get("media") or []:
                    fname = med.get("filename") or med.get("original_filename")
                    if not fname:
                        continue
                    extra = {}
                    if med.get("sticker_alt") or med.get("alt"):
                        extra["alt"] = med.get("sticker_alt") or med.get("alt")
                    if med.get("width"):
                        extra["width"] = med.get("width")
                    if med.get("height"):
                        extra["height"] = med.get("height")
                    if med.get("duration"):
                        extra["duration"] = med.get("duration")
                    if med.get("performer"):
                        extra["performer"] = med.get("performer")
                    if med.get("title"):
                        extra["title"] = med.get("title")
                    if med.get("grouped_id"):
                        extra["grouped_id"] = med.get("grouped_id")
                    if extra:
                        media_extra[fname] = extra
    except Exception as e:
        logger.warning(f"Could not load media extra from archive: {e}")
    
    # Build specs — ONE SPEC PER <attached:> LINE.
    #
    # Telegram binds each uploaded token to an import line by the EXACT
    # file_name, one token per line. Deduplicating by filename (the old `seen`
    # set) collapsed repeated files, so when one archive file was used by N
    # source messages only a single token existed and the other lines imported
    # as literal <attached:> text (proven by real E2E job 49). The serializer
    # now emits unique "{stem}__{msg_id}{ext}" names for repeats and writes a
    # sidecar map; we resolve each line's attach name through that map (or by
    # stripping the __{id} suffix) to the real base file + source message id.
    attach_map: dict[str, dict] = {}
    try:
        # production layout: export_dir/import/import.txt + map beside it
        map_path = export_dir / "import" / "media_attach_map.json"
        if not map_path.exists():
            map_path = export_dir / "media_attach_map.json"  # custom out_file callers
        if map_path.exists():
            attach_map = json.loads(map_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — map is additive
        logger.warning(f"Could not load media_attach_map.json: {e}")

    def _resolve(attach_name: str):
        """(base_filename, source_message_id) for an attach name."""
        hit = attach_map.get(attach_name)
        if hit:
            return hit.get("base_filename") or attach_name, int(hit.get("source_message_id") or 0)
        # fallback: unique names are "m{msg_id}{ext}" (repeated files); the real
        # base file is found via the sidecar map, or by stripping the prefix.
        import re as _re
        m = _re.match(r"^m(\d+)(\.[^.]+)?$", attach_name)
        if m:
            # without the sidecar map we cannot know the original base filename;
            # fall back to searching media dir by the unique name itself.
            return attach_name, int(m.group(1))
        return attach_name, 0

    specs = []
    for fname in wanted_filenames:
        base_name, source_message_id = _resolve(fname)

        # Find the real file in the media directory (by base name).
        file_path = None
        media_type = "document"
        if media_src.exists():
            for type_dir in media_src.iterdir():
                if not type_dir.is_dir():
                    continue
                candidate = type_dir / base_name
                if candidate.exists():
                    file_path = candidate
                    media_type = type_dir.name
                    break

        if not file_path:
            logger.warning(f"Media file not found: {base_name} (attach {fname})")
            continue

        # Get file info
        stat = file_path.stat()
        mime_type = _guess_mime(file_path)
        extra = media_extra.get(base_name, {})
        # grouped_id for album binding comes from the archive media item
        if "grouped_id" in extra and not extra.get("grouped_id"):
            extra.pop("grouped_id", None)

        spec = MediaUploadSpec(
            source_message_id=source_message_id,
            filename=fname,          # upload under the exact attach name
            media_type=media_type,
            mime_type=mime_type,
            file_path=file_path,
            file_size=stat.st_size,
            sha256="",  # Could be computed if needed
            extra=extra,
            grouped_id=extra.get("grouped_id"),
        )
        specs.append(spec)
    
    if limit:
        specs = specs[:limit]
    
    return specs


def _guess_mime(path: Path) -> str:
    """Guess MIME type from file extension."""
    import mimetypes
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


# ---- Convenience function for production import_tasks ----

async def upload_media_for_import(
    client,
    peer,
    import_id: int,
    export_dir: Path,
    import_file: Path,
    output_trace_path: Path,
    limit: Optional[int] = None
) -> MediaImportTrace:
    """
    High-level function to upload all media for an import.
    
    This is the canonical entry point that import_tasks.py should call.
    
    Args:
        client: Telethon client
        peer: Target peer
        import_id: Import ID from initHistoryImport
        export_dir: Export directory
        import_file: Path to import.txt
        output_trace_path: Where to write MEDIA_IMPORT_TRACE.json
        limit: Optional limit
        
    Returns:
        MediaImportTrace
    """
    service = TelegramImportedMediaService(client)
    
    import_text = import_file.read_text(encoding="utf-8")
    specs = build_media_specs_from_archive(export_dir, import_text, limit)
    
    logger.info(f"Uploading {len(specs)} media files for import {import_id}")
    
    trace = await service.upload_all_media(peer, import_id, specs)
    service.write_trace(trace, output_trace_path)
    
    return trace
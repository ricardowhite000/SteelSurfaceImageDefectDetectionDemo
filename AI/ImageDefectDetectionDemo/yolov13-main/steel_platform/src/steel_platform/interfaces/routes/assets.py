from __future__ import annotations

from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/api/v1/projects/{project_id}/assets", tags=["assets"])


def _download_name(relative_path: str | None, asset_id: str) -> str:
    normalized = str(relative_path or asset_id).replace("\\", "/")
    basename = PurePosixPath(normalized).name.replace("\r", "").replace("\n", "")
    return basename or asset_id


def _content_disposition(filename: str, *, attachment: bool) -> str:
    disposition = "attachment" if attachment else "inline"
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r'[^A-Za-z0-9!#$&+.^_`|~()\[\]{}@ -]', "_", ascii_name).strip() or "download"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    encoded_name = quote(filename, safe="!#$&+-.^_`|~")
    return f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'


@router.get("/{asset_id}/content")
def asset_content(
    project_id: str,
    asset_id: str,
    request: Request,
    download: bool = Query(default=False),
) -> StreamingResponse:
    service = request.app.state.services.imports
    asset = service.get_asset(project_id, asset_id)
    filename = _download_name(asset.relative_path, asset.id)
    return StreamingResponse(
        service.open_asset(project_id, asset_id),
        media_type=asset.media_type,
        headers={
            "Content-Disposition": _content_disposition(filename, attachment=download),
            "Content-Length": str(asset.size_bytes),
        },
    )


@router.get("/{asset_id}/thumbnail")
def asset_thumbnail(
    project_id: str,
    asset_id: str,
    request: Request,
    size: int = Query(default=320, ge=160, le=640),
) -> FileResponse:
    path, etag = request.app.state.services.thumbnails.get(project_id, asset_id, size)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"},
    )

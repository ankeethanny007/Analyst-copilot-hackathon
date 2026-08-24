"""Small, provider-neutral Day 1 primitives for durable filing ingestion."""

import hashlib

from fastapi import UploadFile


async def sha256_upload(upload: UploadFile, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Hash an upload without holding a filing in memory, then rewind it for R2."""
    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := await upload.read(chunk_size):
        digest.update(chunk)
        size_bytes += len(chunk)
    await upload.seek(0)
    return digest.hexdigest(), size_bytes

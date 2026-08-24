import io
import asyncio

from fastapi import UploadFile

from backend.app.services.ingestion import sha256_upload


def test_sha256_upload_rewinds_file() -> None:
    asyncio.run(_assert_sha256_upload())


async def _assert_sha256_upload() -> None:
    upload = UploadFile(filename="filing.htm", file=io.BytesIO(b"filing"))
    checksum, size = await sha256_upload(upload, chunk_size=2)
    assert checksum == "a5bfbc7f7c81dc34d961c41578c70c07f12a71c2259102d51d9335eb0e00bbd1"
    assert size == 6
    assert await upload.read() == b"filing"

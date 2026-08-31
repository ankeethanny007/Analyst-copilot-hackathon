import io
import asyncio

from fastapi import UploadFile

from backend.app.services.ingestion import (
    expected_identity_from_filename,
    filing_identity_mismatch,
    identity_from_html,
    sha256_upload,
    validate_upload_identity,
)


def test_sha256_upload_rewinds_file() -> None:
    asyncio.run(_assert_sha256_upload())


async def _assert_sha256_upload() -> None:
    upload = UploadFile(filename="filing.htm", file=io.BytesIO(b"filing"))
    checksum, size = await sha256_upload(upload, chunk_size=2)
    assert checksum == "a5bfbc7f7c81dc34d961c41578c70c07f12a71c2259102d51d9335eb0e00bbd1"
    assert size == 6
    assert await upload.read() == b"filing"


def test_filename_and_inline_xbrl_identity_mismatch_is_actionable() -> None:
    expected = expected_identity_from_filename("3M_2023Q2_10Q.htm")
    actual = identity_from_html(
        """
        <ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentFiscalYearFocus">2023</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentFiscalPeriodFocus">Q1</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentPeriodEndDate">March 31, 2023</ix:nonNumeric>
        """
    )

    message = filing_identity_mismatch("3M_2023Q2_10Q.htm", expected, actual)

    assert message is not None
    assert "expected FY2023 Q2 Form 10-Q" in message
    assert "contains FY2023 Q1 Form 10-Q (period ended March 31, 2023)" in message


def test_matching_upload_identity_is_rewound_and_allowed() -> None:
    asyncio.run(_assert_matching_upload_identity())


async def _assert_matching_upload_identity() -> None:
    content = b"""
        <ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentFiscalYearFocus">2023</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentFiscalPeriodFocus">Q2</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentPeriodEndDate">June 30, 2023</ix:nonNumeric>
    """
    upload = UploadFile(filename="3M_2023Q2_10Q.htm", file=io.BytesIO(content))

    assert await validate_upload_identity(upload, "text/html") is None
    assert await upload.read() == content

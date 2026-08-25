"""Private Cloudflare R2 storage adapter for original filings."""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.client import BaseClient


def client() -> BaseClient:
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")


def storage_key(owner_id: str, checksum: str, filename: str) -> str:
    safe_name = Path(filename or "filing").name.replace(" ", "-")
    return f"filings/{owner_id}/{checksum[:2]}/{checksum}-{safe_name}"


def upload(file_object, key: str, content_type: str) -> None:
    client().upload_fileobj(file_object, os.environ["R2_BUCKET_NAME"], key, ExtraArgs={"ContentType": content_type})


def download(key: str, destination: str) -> None:
    client().download_file(os.environ["R2_BUCKET_NAME"], key, destination)

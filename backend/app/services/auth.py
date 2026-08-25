"""Supabase JWT validation for API requests."""

from __future__ import annotations

import os

import jwt
from fastapi import Header, HTTPException


def current_owner_id(authorization: str | None = Header(default=None)) -> str:
    """Return verified Supabase subject; development may use the seeded owner."""
    if not authorization and os.getenv("APP_ENV", "development") == "development":
        owner_id = os.getenv("DEMO_OWNER_ID")
        if owner_id:
            return owner_id
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "A Supabase access token is required")
    jwks_url = os.getenv("SUPABASE_JWKS_URL")
    issuer_base = os.getenv("SUPABASE_URL")
    if not jwks_url or not issuer_base:
        raise HTTPException(503, "Supabase JWT verification is not configured")
    try:
        key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(authorization[7:]).key
        claims = jwt.decode(authorization[7:], key, algorithms=["ES256", "RS256"], audience="authenticated", issuer=f"{issuer_base.rstrip('/')}/auth/v1")
        return claims["sub"]
    except (jwt.PyJWTError, KeyError) as error:
        raise HTTPException(401, "Invalid Supabase access token") from error

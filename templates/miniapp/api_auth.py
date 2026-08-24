"""Mini App auth route. Copy into api/auth.py.

Two jobs, and nothing else:
  1. verify initData server-side (HMAC-SHA256) and mint a short-lived session
  2. provide the dependency every other route uses to learn who the caller is

No business logic lives here. Routes are thin adapters over services/, exactly
like handlers/ — that is what keeps the bot and the app from disagreeing.

Spec: skills/telegram-miniapp/references/stitching.md
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Request

log = logging.getLogger("api.auth")
router = APIRouter()

BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSION_SECRET = os.environ["SESSION_SECRET"]
SESSION_TTL = int(os.environ.get("SESSION_TTL_SEC", "3600"))
SESSION_MAX_AGE = 86_400          # initData freshness for a normal session
MONEY_MAX_AGE = 3_600             # initData freshness for anything touching money


class AuthError(Exception):
    """Reason is for the log, never for the client. Clients get a plain 401."""


# --------------------------------------------------------------------------- #
# initData
# --------------------------------------------------------------------------- #

def _secret_key() -> bytes:
    # The literal is the KEY, the token is the MESSAGE. Swapped, the hash never
    # matches — and the usual "fix" is to disable validation entirely.
    return hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()


def validate_init_data(init_data: str, *, max_age: int = SESSION_MAX_AGE) -> dict:
    if not init_data:
        raise AuthError("empty initData")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    received, kept = None, []
    for k, v in pairs:
        if k == "hash":
            received = v
        elif k == "signature":
            continue                       # Ed25519 field, not part of the DCS
        else:
            kept.append((k, v))
    if not received:
        raise AuthError("no hash")

    # Values EXACTLY as received: re-encoding them is the #1 cause of mismatch.
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(kept, key=lambda kv: kv[0]))
    expected = hmac.new(_secret_key(), dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise AuthError("hash mismatch")

    data = dict(kept)
    try:
        auth_date = int(data.get("auth_date", ""))
    except ValueError as e:
        raise AuthError("auth_date missing") from e
    age = time.time() - auth_date
    if age > max_age:
        raise AuthError(f"stale auth_date ({int(age)}s)")
    if age < -300:
        raise AuthError("auth_date in the future")

    for field in ("user", "chat", "receiver"):
        if field in data:
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError as e:
                raise AuthError(f"{field} is not JSON") from e
    if not isinstance(data.get("user"), dict) or "id" not in data["user"]:
        raise AuthError("no user")
    return data


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Session:
    user_id: int
    session_id: str
    trace_id: str
    exp: int


def _sign(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(SESSION_SECRET.encode(), body, hashlib.sha256).hexdigest()[:32]
    return body.hex() + "." + sig


def _verify(token: str) -> dict:
    try:
        body_hex, sig = token.split(".", 1)
        body = bytes.fromhex(body_hex)
    except (ValueError, AttributeError) as e:
        raise AuthError("malformed token") from e
    expected = hmac.new(SESSION_SECRET.encode(), body, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        raise AuthError("bad signature")
    payload = json.loads(body)
    if payload.get("exp", 0) < time.time():
        raise AuthError("expired")
    return payload


@router.post("/auth")
async def auth(request: Request):
    body = await request.json()
    trace_id = uuid.uuid4().hex[:12]
    try:
        data = validate_init_data(body.get("init_data", ""))
    except AuthError as e:
        # Log the reason; return nothing useful to a prober.
        log.warning("miniapp.auth", extra={"event": "miniapp.auth", "ok": False,
                                            "reason": str(e), "trace_id": trace_id})
        raise HTTPException(status_code=401, detail="unauthorized") from None

    user = data["user"]
    # from app import services; await services.users.get_or_create(user["id"], ...)
    session_id = uuid.uuid4().hex
    exp = int(time.time()) + SESSION_TTL
    token = _sign({"uid": int(user["id"]), "sid": session_id,
                   "tid": trace_id, "exp": exp})
    log.info("miniapp.auth", extra={"event": "miniapp.auth", "ok": True,
                                    "user_id": int(user["id"]), "trace_id": trace_id,
                                    "detail": body.get("start_param") or None})
    return {"token": token, "ttl": SESSION_TTL, "trace_id": trace_id,
            "user": {"id": user["id"], "first_name": user.get("first_name"),
                     "username": user.get("username")}}


async def current_session(authorization: str = Header(default="")) -> Session:
    """The ONLY source of identity for every other route.

    A route that reads a user id from the body or the query string is a
    vulnerability, and tg_lint --rules miniapp fails the build on it.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        p = _verify(authorization[7:])
    except AuthError as e:
        log.warning("miniapp.auth", extra={"event": "miniapp.auth", "ok": False,
                                            "reason": str(e)})
        raise HTTPException(status_code=401, detail="unauthorized") from None
    return Session(user_id=int(p["uid"]), session_id=p["sid"],
                   trace_id=p["tid"], exp=int(p["exp"]))


async def money_session(request: Request,
                        s: Session = Depends(current_session)) -> Session:
    """Anything touching money re-validates raw initData with a 1h window,
    regardless of session state, and checks it is the same user."""
    body = await request.json()
    try:
        data = validate_init_data(body.get("init_data", ""), max_age=MONEY_MAX_AGE)
    except AuthError as e:
        log.warning("miniapp.auth", extra={"event": "miniapp.auth", "ok": False,
                                            "reason": f"money: {e}"})
        raise HTTPException(status_code=401, detail="unauthorized") from None
    if int(data["user"]["id"]) != s.user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    return s

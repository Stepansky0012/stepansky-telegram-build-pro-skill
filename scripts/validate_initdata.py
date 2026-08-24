#!/usr/bin/env python3
"""validate_initdata — Mini App initData verification, as a library and a CLI.

`initData` is attacker-controlled. Anyone can POST your endpoint with an
arbitrary user.id. This is the only thing standing between a Mini App and an
impersonation vector, so it is also the one function to get exactly right.

Library:
    from validate_initdata import validate, AuthError
    data = validate(init_data, BOT_TOKEN, max_age=3600)   # raises AuthError

CLI:
    python validate_initdata.py --token $BOT_TOKEN --data "query_id=...&hash=..."
    python validate_initdata.py --selftest        # no token or network needed

Exit: 0 valid, 1 invalid, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from urllib.parse import parse_qsl, urlencode

WEBAPP_SECRET_KEY = b"WebAppData"
DEFAULT_MAX_AGE = 86400          # 24h for sessions
MONEY_MAX_AGE = 3600             # 1h for anything touching money


class AuthError(Exception):
    """Never leak the reason to the client. Log it, return a plain 401."""


def data_check_string(pairs: list[tuple[str, str]]) -> str:
    """Alphabetically sorted key=value lines. Values are used EXACTLY as
    received — re-encoding them is the most common cause of a hash mismatch."""
    return "\n".join(f"{k}={v}" for k, v in sorted(pairs, key=lambda kv: kv[0]))


def secret_key(bot_token: str) -> bytes:
    """HMAC_SHA256(message=bot_token, key="WebAppData").

    The order is the thing people get wrong. The literal is the KEY; the token
    is the MESSAGE. Swapped, the hash never matches and the usual "fix" is to
    disable validation entirely.
    """
    return hmac.new(WEBAPP_SECRET_KEY, bot_token.encode(), hashlib.sha256).digest()


def validate(init_data: str, bot_token: str, *,
             max_age: int | None = DEFAULT_MAX_AGE,
             now: float | None = None) -> dict:
    """Return the parsed initData or raise AuthError. Parsed `user` is decoded."""
    if not init_data:
        raise AuthError("empty initData")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    if not pairs:
        raise AuthError("initData is not a query string")

    received = None
    kept: list[tuple[str, str]] = []
    for k, v in pairs:
        if k == "hash":
            received = v
        elif k == "signature":
            continue          # Ed25519 third-party field; not part of the DCS
        else:
            kept.append((k, v))
    if not received:
        raise AuthError("no hash field")

    expected = hmac.new(secret_key(bot_token),
                        data_check_string(kept).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise AuthError("hash mismatch")

    data = dict(kept)
    if max_age:
        try:
            auth_date = int(data.get("auth_date", ""))
        except ValueError as e:
            raise AuthError("auth_date missing or not an integer") from e
        age = (now if now is not None else time.time()) - auth_date
        if age > max_age:
            raise AuthError(f"stale auth_date ({int(age)}s > {max_age}s)")
        if age < -300:
            raise AuthError("auth_date is in the future")

    for field in ("user", "receiver", "chat"):
        if field in data:
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError as e:
                raise AuthError(f"{field} is not valid JSON") from e
    return data


def sign(params: dict, bot_token: str) -> str:
    """Produce valid initData. For TESTS AND FIXTURES ONLY."""
    pairs = [(k, v if isinstance(v, str) else json.dumps(v, separators=(",", ":")))
             for k, v in params.items()]
    h = hmac.new(secret_key(bot_token), data_check_string(pairs).encode(),
                 hashlib.sha256).hexdigest()
    return urlencode(pairs + [("hash", h)])


# --------------------------------------------------------------------------- #

def selftest() -> int:
    token = "123456789:TEST_TOKEN_DO_NOT_USE_IN_PRODUCTION_x"  # tg-lint: ignore[secrets]
    now = 1_700_000_000
    base = {"query_id": "AAF", "auth_date": str(now),
            "user": {"id": 42, "first_name": "Ada", "username": "ada"}}
    good = sign(base, token)
    cases: list[tuple[str, str, bool, str]] = [
        ("valid", good, True, ""),
        ("tampered user id", good.replace("%2242%22", "%2243%22")
                                 .replace("22id%22%3A42", "22id%22%3A43"), False, "hash"),
        ("hash removed", "&".join(p for p in good.split("&")
                                  if not p.startswith("hash=")), False, "no hash"),
        ("wrong token", good, False, "hash"),
        ("stale", sign({**base, "auth_date": str(now - 200_000)}, token), False, "stale"),
        ("future", sign({**base, "auth_date": str(now + 10_000)}, token), False, "future"),
        ("empty", "", False, "empty"),
    ]
    failures = 0
    for name, data, should_pass, expect in cases:
        tok = "999:WRONG_TOKEN_WRONG_TOKEN_WRONG_TOKENxx" if name == "wrong token" else token
        try:
            validate(data, tok, max_age=DEFAULT_MAX_AGE, now=now)
            ok, why = True, ""
        except AuthError as e:
            ok, why = False, str(e)
        passed = ok == should_pass and (should_pass or expect in why)
        failures += not passed
        print(f"{'PASS' if passed else 'FAIL'}  {name:<18} "
              f"{'accepted' if ok else 'rejected: ' + why}")
    print(f"\nselftest: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--token")
    p.add_argument("--data", help="raw initData query string")
    p.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv[1:])

    if a.selftest:
        return selftest()
    if not (a.token and a.data):
        print("need --token and --data (or --selftest)", file=sys.stderr)
        return 2
    try:
        data = validate(a.data, a.token, max_age=a.max_age)
    except AuthError as e:
        print(json.dumps({"valid": False, "reason": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, "data": data}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

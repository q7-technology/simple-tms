"""Command line for what has to happen before anyone can sign in."""
from __future__ import annotations

import argparse
import getpass
import sys
import traceback

from sqlalchemy import select

from tms.db import get_sessionmaker
from tms.models import ApiKey, User
from tms.services import access, api_keys, sessions

MIN_PASSWORD = 12


def cmd_create_user(args) -> int:
    password = args.password or getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD:
        print(f"create-user: the password needs at least {MIN_PASSWORD} characters", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        if db.execute(select(User).where(User.username == args.username)).scalar_one_or_none():
            print(f"create-user: {args.username} already exists", file=sys.stderr)
            return 1
        user = User(username=args.username, display_name=args.display_name or args.username,
                    role=args.role, password_hash=access.hash_password(password),
                    failed_attempts=0, active=True)
        db.add(user)
        db.commit()
        print(f"user {user.username} (id {user.id}) created with role {user.role}")
    return 0


def cmd_create_api_key(args) -> int:
    scopes = [x.strip() for x in args.scopes.split(",") if x.strip()]
    with get_sessionmaker()() as db:
        if db.execute(select(ApiKey).where(ApiKey.name == args.name)).scalar_one_or_none():
            print(f"create-api-key: {args.name} already exists", file=sys.stderr)
            return 1
        try:
            key, raw = api_keys.create(db, args.name, scopes)
        except ValueError as exc:
            print(f"create-api-key: {exc}", file=sys.stderr)
            return 1
        db.commit()
        print(f"api key {key.name} (id {key.id}) created with {', '.join(key.scopes)}")
        print("This key is shown once and never stored:")
        print(raw)
    return 0


def cmd_revoke_api_key(args) -> int:
    with get_sessionmaker()() as db:
        key = db.execute(select(ApiKey).where(ApiKey.name == args.name)).scalar_one_or_none()
        if key is None:
            print(f"revoke-api-key: no key {args.name}", file=sys.stderr)
            return 1
        key.active = False
        db.commit()
        print(f"api key {key.name} revoked")
    return 0


def cmd_seed_demo(args) -> int:
    import logging

    from tms import demo
    from tms.config import get_settings

    # each request the seed makes would be a line of its own
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings = get_settings()
    skus = []
    for part in args.skus.split(","):
        sku, _, uom = part.strip().partition(":")
        skus.append((sku, uom or "EA"))
    progress = demo.Progress()
    try:
        demo.check_empty()
        wms = demo.Wms(settings.wms_url, settings.wms_api_key, warehouse=args.warehouse,
                       operator=args.operator, skus=skus)
        print(f"creating {len(demo.ALL)} deliveries in Simple WMS at {settings.wms_url}, and "
              "packing them once the stock for all is reserved, then the day; the run on the "
              f"road drives for about {args.pace * 2.5:g} min")
        made = demo.seed_demo(wms, pace_minutes=args.pace, wait_seconds=args.wait,
                              progress=progress)
    except Exception as e:
        if isinstance(e, demo.SeedError):
            print(f"seed-demo: {e}", file=sys.stderr)
        else:
            traceback.print_exc()
        if half_made := progress.half_made():
            print(half_made, file=sys.stderr)
        return 1
    for line in demo.summary(made):
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tms")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="create a desktop user")
    p.add_argument("--username", required=True)
    p.add_argument("--display-name")
    p.add_argument("--role", default="admin", choices=list(sessions.ROLES))
    p.add_argument("--password", help="prompted for if omitted")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("create-api-key", help="create a key for a system (printed once)")
    p.add_argument("--name", required=True)
    p.add_argument("--scopes", required=True,
                   help=f"comma separated, some of {','.join(api_keys.SCOPES)}")
    p.set_defaults(func=cmd_create_api_key)

    p = sub.add_parser("revoke-api-key", help="stop a key working (it is kept, not deleted)")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_revoke_api_key)

    p = sub.add_parser("seed-demo", help="fill an empty database with a demo day, through the API, "
                                          "with freight packed in Simple WMS")
    p.add_argument("--skus", default="GLV-L:PR,VST-HV,HAT-WH,TIE-300:PK",
                   help="Simple WMS SKUs with stock in the warehouse, as SKU or SKU:UOM "
                        "(EA unless given), comma separated; the default is wms seed-demo's")
    p.add_argument("--warehouse", default="BAL-WH01", help="the Simple WMS warehouse it ships from")
    p.add_argument("--operator", default="op-017", help="the Simple WMS operator who picks and packs")
    p.add_argument("--pace", type=float, default=3,
                   help="minutes the run on the road takes to each of its first two stops")
    p.add_argument("--wait", type=float, default=120,
                   help="seconds to wait for Simple WMS's delivery.packed events")
    p.set_defaults(func=cmd_seed_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

"""Command line for what has to happen before anyone can sign in."""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from tms.db import get_sessionmaker
from tms.models import User
from tms.services import access, sessions

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tms")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="create a desktop user")
    p.add_argument("--username", required=True)
    p.add_argument("--display-name")
    p.add_argument("--role", default="admin", choices=list(sessions.ROLES))
    p.add_argument("--password", help="prompted for if omitted")
    p.set_defaults(func=cmd_create_user)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

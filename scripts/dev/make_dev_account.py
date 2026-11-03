#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_dev_account.py -- create (or repair) a DEVELOPER account on this box.

Run it on the board:

    cd ~/irivai-systor && python3 scripts/dev/make_dev_account.py

Why this exists as a script rather than a documented snippet: a developer
account is three edits that must all land -- the account itself, the
`developer` flag, and NO role -- and a hand-typed snippet that stops halfway
leaves an account that logs in but cannot reach the pages it exists for.

WHY IT IS NOT AN API. The developer tier is deliberately unreachable from the
web UI (see core/services/auth.py DEVELOPER_FLAG): no route grants it and
update_user() refuses to touch it. The only way in is running this ON the box,
which needs shell access. That is what keeps a stolen admin session from
escalating -- an attacker who owns the admin account gets a deployment, not the
product's shipped content.

The password is PROMPTED, never taken as an argument: an argv password lands in
shell history and in the process list, where any other user on the box can read
it. It is stored salted+hashed like every other account, in
config/users.local.toml, which is gitignored -- so a developer account never
travels to another deployment in a commit.
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config                                     # noqa: E402
from core.services import auth                              # noqa: E402


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    username = (argv[0] if argv else "dev").strip().lower()

    # Not the administrator's name -- whatever this deployment renamed it to.
    if not username.isidentifier() or username == auth.admin_username():
        print(f"refusing {username!r}: use a simple word, and not "
              f"{auth.admin_username()!r} (the administrator account)")
        return 2

    existing = auth.users().get(username)
    if existing:
        print(f"{username!r} already exists.")
        if input("Reset its password and re-apply the developer flag? [y/N] "
                 ).strip().lower() not in ("y", "yes"):
            print("nothing changed")
            return 1

    password = getpass.getpass(f"password for {username} (min 8): ")
    if len(password) < 8:
        print("password must be at least 8 characters")
        return 2
    if password != getpass.getpass("repeat: "):
        print("passwords did not match")
        return 2

    if existing:
        # NOT auth.set_user_password(): that refuses on a developer account, on
        # purpose (an admin resetting the developer's password would simply log
        # in as them). This script IS the sanctioned path -- it needs shell
        # access to the box -- so it sets the hash itself.
        import secrets
        local = config.load_local("users")
        salt = secrets.token_hex(8)
        local["users"][username]["salt"] = salt
        local["users"][username]["password_hash"] = auth._hash(password, salt)
        config.save_local("users", local)
    else:
        auth.create_user(username, password, "*")

    # The two things create_user cannot do, because no API may: raise the
    # account to the developer tier, and take away the role it was given.
    # A developer has no role -- roles describe the people who USE a
    # deployment, and this account is the one that built it.
    local = config.load_local("users")
    record = local["users"][username]
    record[auth.DEVELOPER_FLAG] = True
    record.pop("role", None)
    record["enabled"] = True
    config.save_local("users", local)

    ok = auth.is_developer(username) and auth.verify_login(username, password)
    print(f"\n{username}: developer={auth.is_developer(username)} "
          f"role={auth.role_label(username)} login={'ok' if ok else 'FAILED'}")
    print("stored in config/users.local.toml (gitignored, password hashed)")
    print("restart the hub for the change to reach a running process")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

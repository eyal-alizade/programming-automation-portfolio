import argparse
import logging
import os
import sqlite3
import sys
import time
from . import __version__
from .db import Store
from .demo import run_demo, simulate
from .menus import configure_command_menu
from .runner import Runner
from .service import BotService
from .transport import TelegramAPI, TransportError


def main(argv=None):
    parser = argparse.ArgumentParser(description="DeskBuddy: a persistent business request bot. Offline by default.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the complete synthetic offline scenario")
    demo.add_argument("--json", action="store_true")
    demo.add_argument("--db", help="optional NEW database for inspecting synthetic demo records")
    simulator = commands.add_parser("simulate", help="interactive offline customer/admin chat")
    simulator.add_argument("--db", help="optional database for resuming the offline session")
    web = commands.add_parser("web-demo", help="local browser demo with synthetic customer and admin roles")
    web.add_argument("--port", type=int, default=8812)
    poll = commands.add_parser("poll", help="explicitly connect your own configured bot to Telegram")
    poll.add_argument("--db", default=".deskbuddy/live.sqlite3")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            run_demo(args.db, args.json)
        elif args.command == "simulate":
            simulate(args.db)
        elif args.command == "web-demo":
            from .web_demo import run_web_demo
            run_web_demo(args.port)
        else:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            raw_admins = os.environ.get("DESKBUDDY_ADMIN_IDS", "")
            if not raw_admins or any(not v.strip().isascii() or not v.strip().isdecimal() for v in raw_admins.split(",")):
                raise ValueError("Set DESKBUDDY_ADMIN_IDS to your authorized numeric administrator IDs")
            admin_ids = {int(v.strip()) for v in raw_admins.split(",")}
            if not admin_ids or len(admin_ids) > 20 or min(admin_ids) <= 0:
                raise ValueError("Configure between 1 and 20 positive administrator IDs")
            api = TelegramAPI(token)
            # Authentication is checked only in the explicitly selected network mode.
            bot = api.get_me()
            store = Store(args.db)
            try:
                store.bind_runtime("telegram", bot["id"])
                logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
                runner = Runner(BotService(store, admin_ids), api)
                delay = runner.flush()
                if delay:
                    time.sleep(delay)
                configure_command_menu(api, admin_ids)
                runner.run_forever()
            finally:
                store.close()
    except KeyboardInterrupt:
        if args.command == "web-demo":
            print("\nLocal demo stopped. Temporary demo data was removed.", file=sys.stderr)
        else:
            print("\nDeskBuddy stopped. Committed state and pending replies are retained.", file=sys.stderr)
        return 0
    except (ValueError, OSError, sqlite3.Error, TransportError) as error:
        # Transport errors deliberately contain no URL, token or remote error body.
        print(f"DeskBuddy: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Log in to your GE Cync account once and save tokens for the Cync plug driver.

    python scripts/cync_login.py

Asks for your Cync email and password (the password is never stored), then the 6-digit code Cync emails
you. Saves tokens to data/cync_token.json (git-ignored) and lists the devices on the account so you can
set PLUG_NAME in .env. Close the Cync phone app first: Cync allows one connection per account.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet.config import CYNC_TOKEN_PATH  # noqa: E402
from three_feet.safety.cync_plug import device_is_on, save_tokens  # noqa: E402


async def main() -> int:
    try:
        import aiohttp
        from pycync import Auth, Cync
        from pycync.exceptions import AuthFailedError, TwoFactorRequiredError
    except ImportError:
        print('pycync is not installed. Run: pip install -e ".[cync]"')
        return 1

    email = input("Cync email: ").strip()
    password = getpass.getpass("Cync password (not saved): ")
    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username=email, password=password)
        try:
            await auth.login()
        except TwoFactorRequiredError:
            code = input("Cync emailed you a code. Enter it: ").strip()
            try:
                await auth.login(code)
            except AuthFailedError as exc:
                print(f"Login failed: {exc}")
                return 1
        except AuthFailedError as exc:
            print(f"Login failed: {exc}")
            return 1
        save_tokens(auth.user, CYNC_TOKEN_PATH)
        print(f"Saved tokens to {CYNC_TOKEN_PATH}")

        cync = await Cync.create(auth)
        await asyncio.sleep(4)  # let the connection log in and probe devices
        print("\nDevices on this account:")
        for d in cync.get_devices():
            print(f"  name={getattr(d, 'name', '?')!r:28} class={type(d).__name__:10} "
                  f"type_id={getattr(d, 'device_type_id', '?')} online={getattr(d, 'is_online', '?')} "
                  f"on={device_is_on(d)}")
        print('\nSet PLUG_KIND=cync and PLUG_NAME="<the plug name above>" in .env,'
              " then test with: python scripts/plug_test.py")
        try:
            await asyncio.wait_for(cync.shut_down(), 3)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

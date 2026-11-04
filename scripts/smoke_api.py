"""Check the Meta Model API key and that the models Three Feet needs are enabled.

    python scripts/smoke_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet_app.config import get_settings  # noqa: E402
from three_feet_app.perception.meta_client import make_client  # noqa: E402


def main() -> int:
    s = get_settings()
    client = make_client(s)
    available = {m.id for m in client.models.list()}
    ok = True
    for model in (s.sam_model, s.spark_model, s.wonder_model):
        found = model in available
        ok &= found
        print(f"{'OK     ' if found else 'MISSING'} {model}")
    if not ok:
        print("\nAvailable:", ", ".join(sorted(available)))
        return 1
    reply = client.chat.completions.create(
        model=s.spark_model, messages=[{"role": "user", "content": "Reply with exactly: ready"}]
    )
    print("muse-spark says:", (reply.choices[0].message.content or "").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

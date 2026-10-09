"""Export the same local group policy for Secret Manager, without uploading it."""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setting


def main():
    policy = getattr(setting, "META_KEY_GROUPS", None)
    if not isinstance(policy, dict):
        raise SystemExit("setting.py に META_KEY_GROUPS を設定してください。")
    folder = ROOT / ".local" / "ip-access"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = folder / "server-settings.json"
    # Export only the configured policy. Environment references remain references;
    # no local .env token is read, resolved or transferred by this command.
    payload = {"META_KEY_GROUPS_JSON": json.dumps(policy, ensure_ascii=False)}
    fd, temporary = tempfile.mkstemp(prefix=".server-settings-", dir=folder)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(".local/ip-access/server-settings.json に保存しました。")


if __name__ == "__main__":
    main()

"""Generate local deployment credentials without printing secrets. Refuses overwrites."""

import json
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
(root / "var").mkdir(exist_ok=True)
env_path, credential_path = root / ".env", root / "var/access.json"
if env_path.exists() or credential_path.exists():
    raise SystemExit("已有 .env 或 var/access.json；请保留现有凭据，或选择新的部署目录。")
credentials = {role: secrets.token_urlsafe(36) for role in ("operator", "approver", "viewer")}
tokens = {
    token: {"user": role + ".local", "role": role, "tenant": "factory"} for role, token in credentials.items()
}
password = secrets.token_urlsafe(32)
text = f"ONTOBRIDGE_TOKENS='{json.dumps(tokens, separators=(',', ':'))}'\nPOSTGRES_PASSWORD={password}\n"
for path, content in [(env_path, text), (credential_path, json.dumps(credentials, indent=2) + "\n")]:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
print("已创建 .env 和 var/access.json（权限 0600）；凭据未输出到终端。")

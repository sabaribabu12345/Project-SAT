from __future__ import annotations

import os
import sys
import unicodedata
import urllib.parse
import urllib.request
import json
import base64
from pathlib import Path


def _sanitize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    # Remove invisible format characters often introduced by password managers/copy-paste.
    sanitized = "".join(ch for ch in value if unicodedata.category(ch) != "Cf").strip()
    return sanitized or None


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _load_config_values() -> dict[str, str | None]:
    project_root = Path(__file__).resolve().parents[2]
    env_path = project_root / ".env"
    file_values = _read_env_file(env_path)

    def get(key: str) -> str | None:
        # Prefer project-local .env over inherited shell environment to avoid stale exports.
        file_value = file_values.get(key)
        if file_value is not None:
            return _sanitize_secret(file_value)
        return _sanitize_secret(os.getenv(key))

    return {
        "host": get("DATABRICKS_HOST"),
        "auth_type": get("DATABRICKS_AUTH_TYPE"),
        "client_id": get("DATABRICKS_CLIENT_ID"),
        "client_secret": get("DATABRICKS_CLIENT_SECRET"),
        "token": get("DATABRICKS_TOKEN"),
    }


def _oauth_token(host: str, client_id: str, client_secret: str) -> str:
    token_url = f"{host.rstrip('/')}/oidc/v1/token"
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "all-apis"}).encode("utf-8")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        token_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = str(payload.get("access_token", "")).strip()
    if not token:
        raise ValueError("OAuth token endpoint returned no access_token")
    return token


def main() -> None:
    cfg = _load_config_values()

    try:
        if cfg["token"]:
            token = cfg["token"]
        else:
            if not cfg["host"] or not cfg["client_id"] or not cfg["client_secret"]:
                raise ValueError("Missing host/client_id/client_secret for OAuth M2M")
            token = _oauth_token(cfg["host"], cfg["client_id"], cfg["client_secret"])
    except Exception as exc:
        error_text = str(exc)
        print("Failed to resolve Databricks OAuth access token")
        print(f"error={error_text}")
        if "invalid_client" in error_text:
            print("")
            print("Troubleshooting invalid_client:")
            print("1) Confirm OAuth client ID/secret pair is valid and active.")
            print("2) Use secret VALUE, not secret identifier.")
            print("3) Regenerate secret and update .env.")
            print("4) Confirm DATABRICKS_HOST targets the same workspace where SP is configured.")
        print("Ensure DATABRICKS_HOST and OAuth M2M vars are set in .env:")
        print("DATABRICKS_AUTH_TYPE=oauth-m2m")
        print("DATABRICKS_CLIENT_ID=...")
        print("DATABRICKS_CLIENT_SECRET=...")
        sys.exit(1)

    # Print only the token so this can be command-substituted safely.
    print(token)


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import json
import base64
from pathlib import Path


def _sanitize_secret(value: str | None) -> str | None:
    if value is None:
        return None
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


def _whoami(host: str, token: str) -> dict[str, object]:
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/2.0/preview/scim/v2/Me",
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    cfg = _load_config_values()

    try:
        host = (cfg["host"] or "").rstrip("/")
        if not host:
            raise ValueError("Missing DATABRICKS_HOST")

        if cfg["token"]:
            access_token = cfg["token"]
        else:
            if not cfg["client_id"] or not cfg["client_secret"]:
                raise ValueError("Missing OAuth M2M credentials")
            access_token = _oauth_token(host, cfg["client_id"], cfg["client_secret"])

        me = _whoami(host, access_token)
    except Exception as exc:
        error_text = str(exc)
        print("Databricks connectivity FAILED")
        print(f"error={error_text}")
        if "invalid_client" in error_text:
            print("")
            print("Troubleshooting invalid_client:")
            print("1) Verify DATABRICKS_CLIENT_ID is the OAuth client ID (not display name/application name).")
            print("2) Verify DATABRICKS_CLIENT_SECRET is the OAuth secret VALUE (not secret ID).")
            print("3) Regenerate a fresh OAuth secret and paste it directly into .env.")
            print("4) Ensure the service principal is added to this workspace.")
            print("5) Ensure DATABRICKS_HOST matches the target workspace URL exactly.")
        print("Set one of the following in .env:")
        print("- PAT auth: DATABRICKS_HOST + DATABRICKS_TOKEN")
        print("- OAuth M2M: DATABRICKS_HOST + DATABRICKS_AUTH_TYPE=oauth-m2m + DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET")
        print("Or configure ~/.databrickscfg.")
        sys.exit(1)

    print("Databricks connectivity OK")
    print(f"workspace_host={host}")
    print(f"user={me.get('userName', '<service-principal>')}")


if __name__ == "__main__":
    main()

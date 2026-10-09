"""Access-token resolution for the three supported auth methods.

* ``agent`` (default) — read the token the local Infisical Agent deposits
  into its file *sink* (``agent_token_path``).
* ``universal-auth`` — Machine Identity client ID/secret (env vars or
  files) exchanged for a short-lived token via the login endpoint.
* ``token`` — static token from ``INFISICAL_TOKEN``.

Tokens live in memory only and are never logged; error messages name
paths and env vars, never credential values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import requests

from agent.secret_sources.base import get_source_environment

from .errors import ErrorKind, InfisicalError

DEFAULT_AGENT_TOKEN_PATH = "~/.infisical/agent-token"
CLIENT_ID_ENV = "INFISICAL_CLIENT_ID"
CLIENT_SECRET_ENV = "INFISICAL_CLIENT_SECRET"
STATIC_TOKEN_ENV = "INFISICAL_TOKEN"
LOGIN_PATH = "/api/v1/auth/universal-auth/login"

_METHODS = ("agent", "universal-auth", "token")


def resolve_token(
    auth_cfg: dict,
    host_url: str,
    session: Optional[requests.Session] = None,
    timeout: float = 30.0,
) -> str:
    """Return a bearer token per the configured method, or raise."""
    if not isinstance(auth_cfg, dict):
        auth_cfg = {}
    method = str(auth_cfg.get("method") or "agent")
    if method == "agent":
        return _agent_token(auth_cfg)
    if method == "universal-auth":
        return _universal_auth_token(auth_cfg, host_url, session, timeout)
    if method == "token":
        return _static_token()
    raise InfisicalError(
        ErrorKind.NOT_CONFIGURED,
        f"unknown auth.method {method!r} (expected one of {', '.join(_METHODS)})",
    )


def _agent_token(auth_cfg: dict) -> str:
    raw_path = str(auth_cfg.get("agent_token_path") or DEFAULT_AGENT_TOKEN_PATH)
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise InfisicalError(
            ErrorKind.NOT_CONFIGURED,
            f"agent token sink file not found at {path} "
            "(is the Infisical Agent running, and does auth.agent_token_path "
            "match its sink config?)",
        )
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise InfisicalError(
            ErrorKind.NOT_CONFIGURED, f"agent token sink file at {path} is empty"
        )
    return token


def _read_credential(auth_cfg: dict, env_name: str, path_key: str) -> str:
    """A credential comes from an env var, or a file named in the config."""
    value = get_source_environment().get(env_name, "").strip()
    if value:
        return value
    raw_path = auth_cfg.get(path_key)
    if raw_path:
        path = Path(str(raw_path)).expanduser()
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        raise InfisicalError(
            ErrorKind.NOT_CONFIGURED,
            f"auth.{path_key} file at {path} is missing or empty",
        )
    raise InfisicalError(
        ErrorKind.NOT_CONFIGURED,
        f"universal-auth needs {env_name} set or auth.{path_key} configured",
    )


def _universal_auth_token(
    auth_cfg: dict,
    host_url: str,
    session: Optional[requests.Session],
    timeout: float,
) -> str:
    client_id = _read_credential(auth_cfg, CLIENT_ID_ENV, "client_id_path")
    client_secret = _read_credential(auth_cfg, CLIENT_SECRET_ENV, "client_secret_path")

    http = session or requests.Session()
    url = host_url.rstrip("/") + LOGIN_PATH
    try:
        resp = http.post(
            url,
            json={"clientId": client_id, "clientSecret": client_secret},
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise InfisicalError(
            ErrorKind.TIMEOUT, f"universal-auth login timed out after {timeout:.0f}s"
        ) from exc
    except requests.RequestException as exc:
        raise InfisicalError(
            ErrorKind.NETWORK, f"universal-auth login failed to reach {url}: {exc}"
        ) from exc

    if resp.status_code in (400, 401, 403):
        raise InfisicalError(
            ErrorKind.AUTH_FAILED,
            f"universal-auth login rejected (HTTP {resp.status_code}) — "
            "check the machine identity client ID/secret",
        )
    if resp.status_code != 200:
        raise InfisicalError(
            ErrorKind.INTERNAL,
            f"universal-auth login returned unexpected HTTP {resp.status_code}",
        )

    try:
        payload = resp.json()
    except ValueError:
        payload = None
    token = payload.get("accessToken", "") if isinstance(payload, dict) else ""
    if not isinstance(token, str) or not token:
        raise InfisicalError(
            ErrorKind.INTERNAL, "universal-auth login response had no accessToken"
        )
    return token


def _static_token() -> str:
    token = get_source_environment().get(STATIC_TOKEN_ENV, "").strip()
    if not token:
        raise InfisicalError(
            ErrorKind.NOT_CONFIGURED,
            f"auth.method is 'token' but {STATIC_TOKEN_ENV} is not set",
        )
    return token

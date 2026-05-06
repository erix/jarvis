"""OpenAI Codex OAuth device-code login and token refresh.

This mirrors the Codex subscription route used by OpenClaw's openai-codex
provider: auth.openai.com device auth, then chatgpt.com/backend-api/codex.
"""
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

AUTH_BASE_URL = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEVICE_CALLBACK_URL = f"{AUTH_BASE_URL}/deviceauth/callback"
DEVICE_TIMEOUT_SECONDS = 15 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 5

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_PATH = os.getenv(
    "JARVIS_CODEX_TOKEN_PATH",
    os.path.join(_ROOT, ".jarvis", "codex_oauth.json"),
)


@dataclass
class CodexCredentials:
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 60


@dataclass
class CodexDeviceCode:
    device_auth_id: str
    user_code: str
    verification_url: str
    interval_seconds: int
    expires_at: float


def _headers(content_type: str) -> dict:
    return {
        "Content-Type": content_type,
        "originator": "jarvis",
        "User-Agent": "jarvis",
    }


def _parse_json_response(response: requests.Response, prefix: str) -> dict:
    text = response.text
    if not response.ok:
        try:
            body = response.json()
            detail = body.get("error_description") or body.get("error") or text
        except Exception:
            detail = text
        raise RuntimeError(f"{prefix}: HTTP {response.status_code} {detail}".strip())
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"{prefix}: invalid JSON response") from exc


def load_credentials(path: str = TOKEN_PATH) -> Optional[CodexCredentials]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_at = data.get("expires_at")
    if not access or not refresh or not isinstance(expires_at, (int, float)):
        return None
    return CodexCredentials(access_token=access, refresh_token=refresh, expires_at=float(expires_at))


def save_credentials(credentials: CodexCredentials, path: str = TOKEN_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "access_token": credentials.access_token,
                "refresh_token": credentials.refresh_token,
                "expires_at": credentials.expires_at,
            },
            f,
            indent=2,
        )
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def refresh_credentials(credentials: CodexCredentials) -> CodexCredentials:
    response = requests.post(
        f"{AUTH_BASE_URL}/oauth/token",
        headers=_headers("application/x-www-form-urlencoded"),
        data={
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "client_id": CODEX_CLIENT_ID,
        },
        timeout=30,
    )
    body = _parse_json_response(response, "OpenAI Codex token refresh failed")
    access = body.get("access_token")
    refresh = body.get("refresh_token") or credentials.refresh_token
    expires_in = float(body.get("expires_in") or 3600)
    if not access:
        raise RuntimeError("OpenAI Codex token refresh did not return an access token")
    refreshed = CodexCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + expires_in,
    )
    save_credentials(refreshed)
    return refreshed


def get_valid_credentials() -> CodexCredentials:
    credentials = load_credentials()
    if not credentials:
        raise RuntimeError(
            "Codex OAuth credentials not found. Run `python run_codex_login.py` first."
        )
    if credentials.is_expired:
        credentials = refresh_credentials(credentials)
    return credentials


def request_device_code() -> CodexDeviceCode:
    response = requests.post(
        f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode",
        headers=_headers("application/json"),
        json={"client_id": CODEX_CLIENT_ID},
        timeout=30,
    )
    body = _parse_json_response(response, "OpenAI Codex device-code request failed")
    device_auth_id = body.get("device_auth_id")
    user_code = body.get("user_code") or body.get("usercode")
    interval = int(body.get("interval") or DEFAULT_POLL_INTERVAL_SECONDS)
    if not device_auth_id or not user_code:
        raise RuntimeError("OpenAI Codex device-code response was missing required fields")
    return CodexDeviceCode(
        device_auth_id=device_auth_id,
        user_code=user_code,
        verification_url=f"{AUTH_BASE_URL}/codex/device",
        interval_seconds=interval,
        expires_at=time.time() + DEVICE_TIMEOUT_SECONDS,
    )


def poll_device_authorization_once(device_code: CodexDeviceCode) -> Optional[tuple[str, str]]:
    if time.time() >= device_code.expires_at:
        raise RuntimeError("OpenAI Codex device authorization timed out")
    response = requests.post(
        f"{AUTH_BASE_URL}/api/accounts/deviceauth/token",
        headers=_headers("application/json"),
        json={"device_auth_id": device_code.device_auth_id, "user_code": device_code.user_code},
        timeout=30,
    )
    if response.status_code in (403, 404):
        return None
    body = _parse_json_response(response, "OpenAI Codex device authorization failed")
    authorization_code = body.get("authorization_code")
    code_verifier = body.get("code_verifier")
    if not authorization_code or not code_verifier:
        raise RuntimeError("OpenAI Codex device authorization response was missing required fields")
    return authorization_code, code_verifier


def exchange_device_authorization(authorization_code: str, code_verifier: str) -> CodexCredentials:
    response = requests.post(
        f"{AUTH_BASE_URL}/oauth/token",
        headers=_headers("application/x-www-form-urlencoded"),
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": DEVICE_CALLBACK_URL,
            "client_id": CODEX_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    body = _parse_json_response(response, "OpenAI Codex token exchange failed")
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    expires_in = float(body.get("expires_in") or 3600)
    if not access or not refresh:
        raise RuntimeError("OpenAI Codex token exchange did not return OAuth tokens")

    credentials = CodexCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + expires_in,
    )
    save_credentials(credentials)
    return credentials


def login_device_code() -> CodexCredentials:
    device_code = request_device_code()
    print()
    print("Open this URL and enter the code:")
    print(f"  {device_code.verification_url}")
    print()
    print(f"Code: {device_code.user_code}")
    print()

    authorization_code = None
    code_verifier = None
    while time.time() < device_code.expires_at:
        time.sleep(max(1, device_code.interval_seconds))
        authorization = poll_device_authorization_once(device_code)
        if authorization is None:
            continue
        authorization_code, code_verifier = authorization
        break

    if not authorization_code or not code_verifier:
        raise RuntimeError("OpenAI Codex device authorization timed out")
    return exchange_device_authorization(authorization_code, code_verifier)

"""Dashboard settings for AI provider and Codex OAuth."""
import os
from datetime import datetime
from typing import Optional

import streamlit as st

from analysis.ai_settings import load_ai_settings, save_ai_settings
from analysis.codex_oauth import (
    CodexDeviceCode,
    TOKEN_PATH,
    exchange_device_authorization,
    load_credentials,
    poll_device_authorization_once,
    request_device_code,
)


def _fmt_expiry(expires_at: float) -> str:
    return datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")


def _current_provider(settings: dict) -> str:
    return os.getenv("JARVIS_AI_PROVIDER") or os.getenv("AI_PROVIDER") or settings.get("provider") or "openrouter"


def _current_model(settings: dict, provider: str) -> str:
    return (
        os.getenv("JARVIS_MODEL")
        or (os.getenv("CODEX_MODEL") if provider == "codex" else None)
        or settings.get("model")
        or ("gpt-5.5" if provider == "codex" else "anthropic/claude-sonnet-4-6")
    )


def _device_code_from_state() -> Optional[CodexDeviceCode]:
    data = st.session_state.get("codex_device_code")
    if not isinstance(data, dict):
        return None
    try:
        return CodexDeviceCode(
            device_auth_id=data["device_auth_id"],
            user_code=data["user_code"],
            verification_url=data["verification_url"],
            interval_seconds=int(data["interval_seconds"]),
            expires_at=float(data["expires_at"]),
        )
    except Exception:
        return None


def _store_device_code(device_code: CodexDeviceCode) -> None:
    st.session_state.codex_device_code = {
        "device_auth_id": device_code.device_auth_id,
        "user_code": device_code.user_code,
        "verification_url": device_code.verification_url,
        "interval_seconds": device_code.interval_seconds,
        "expires_at": device_code.expires_at,
    }


def render():
    st.markdown("## Settings")

    settings = load_ai_settings()
    provider = _current_provider(settings)
    provider_options = ["openrouter", "codex"]
    selected_provider = st.radio(
        "AI provider",
        provider_options,
        index=provider_options.index(provider) if provider in provider_options else 0,
        horizontal=True,
    )
    default_model = _current_model(settings, selected_provider)
    model = st.text_input("Model", value=default_model)

    if st.button("Save AI settings", type="primary"):
        save_ai_settings({"provider": selected_provider, "model": model})
        os.environ["JARVIS_AI_PROVIDER"] = selected_provider
        os.environ["JARVIS_MODEL"] = model
        st.success("AI settings saved.")

    st.markdown("---")
    st.markdown("### Codex OAuth")

    credentials = load_credentials()
    if credentials:
        status = "Expired" if credentials.is_expired else "Ready"
        st.metric("Codex token", status)
        st.caption(f"Expires: {_fmt_expiry(credentials.expires_at)}")
        st.caption(f"Stored at: {TOKEN_PATH}")
    else:
        st.warning("No Codex OAuth token found.")

    left, right = st.columns([1, 1])
    with left:
        if st.button("Start Codex login"):
            try:
                device_code = request_device_code()
                _store_device_code(device_code)
                st.success("Device code created.")
            except Exception as exc:
                st.error(f"Could not start Codex login: {exc}")

    device_code = _device_code_from_state()
    if device_code:
        st.markdown(f"Open [{device_code.verification_url}]({device_code.verification_url})")
        st.code(device_code.user_code)
        st.caption(f"Expires: {_fmt_expiry(device_code.expires_at)}")

    with right:
        if st.button("Check login"):
            device_code = _device_code_from_state()
            if not device_code:
                st.error("Start Codex login first.")
            else:
                try:
                    authorization = poll_device_authorization_once(device_code)
                    if authorization is None:
                        st.info("Authorization is still pending.")
                    else:
                        credentials = exchange_device_authorization(*authorization)
                        st.session_state.pop("codex_device_code", None)
                        st.success(f"Codex login complete. Token expires {_fmt_expiry(credentials.expires_at)}.")
                except Exception as exc:
                    st.error(f"Could not complete Codex login: {exc}")

    if st.button("Test AI provider"):
        try:
            from analysis.api_client import APIClient

            client = APIClient(provider=selected_provider, model=model)
            text = client.chat_text(
                system_prompt="You are JARVIS. Reply with one concise sentence.",
                user_prompt="Confirm the AI provider is reachable.",
                max_tokens=80,
                analyzer_type="settings_test",
            )
            st.success(text or "Provider returned an empty response.")
        except Exception as exc:
            st.error(f"Provider test failed: {exc}")

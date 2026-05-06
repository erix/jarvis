#!/usr/bin/env python3
"""Login to OpenAI Codex OAuth for subscription-backed JARVIS AI calls."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from analysis.codex_oauth import TOKEN_PATH, login_device_code


def main() -> None:
    credentials = login_device_code()
    print(f"Codex OAuth credentials saved to {TOKEN_PATH}")
    print(f"Access token expires at unix time {int(credentials.expires_at)}")


if __name__ == "__main__":
    main()

"""Launch the JARVIS Streamlit dashboard.

Usage:
  python run_dashboard.py
  python run_dashboard.py --port 8502
"""
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Launch JARVIS dashboard")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    app_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    if not os.path.exists(app_path):
        print(f"ERROR: dashboard/app.py not found at {app_path}")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "streamlit", "run",
        app_path,
        "--server.port", str(args.port),
        "--server.address", args.host,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--theme.base", "dark",
        "--theme.backgroundColor", "#0b0e17",
        "--theme.primaryColor", "#6366f1",
        "--theme.textColor", "#e2e8f0",
    ]

    print(f"Launching JARVIS dashboard at http://{args.host}:{args.port}")
    try:
        subprocess.run(cmd, cwd=os.path.dirname(__file__))
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


if __name__ == "__main__":
    main()

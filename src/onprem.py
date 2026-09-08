"""Run the UI on loopback only, without telemetry or runtime installation."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="trustee-fds 로컬 실행")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port는 1024~65535 범위여야 합니다.")
    if importlib.util.find_spec("streamlit") is None:
        print("Streamlit이 없습니다. 배포 안내에 따라 requirements-ui.txt를 설치해 주세요.", file=sys.stderr)
        return 2
    from src.ui_service import analyze, sample_files
    _, _, results, blocked = analyze(sample_files())
    if blocked:
        return 2
    if args.check_only:
        print(f"OK: transactions={len(results)}, candidates={sum(r.candidate for r in results)}")
        return 0
    print(f"브라우저에서 http://127.0.0.1:{args.port} 를 여세요. 종료: Ctrl+C", flush=True)
    env = dict(os.environ, STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    command = [sys.executable, "-m", "streamlit", "run", str(ROOT / "streamlit_app.py"),
               "--server.address=127.0.0.1", f"--server.port={args.port}",
               "--server.headless=true", "--browser.gatherUsageStats=false",
               "--server.fileWatcherType=none", "--server.enableCORS=true",
               "--server.enableXsrfProtection=true"]
    try:
        return subprocess.call(command, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

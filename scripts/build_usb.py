"""Build an offline source bundle on each target OS/Python/architecture.

Python itself is a prerequisite; this is not a standalone executable.
"""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git_state():
    """배포본이 어느 커밋에서 나왔는지 기록한다. git이 없으면(ZIP 소스 등) None."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, bool(status.strip())


def main():
    label = f"trustee-fds-{platform.system()}-{platform.machine()}-py{sys.version_info.major}{sys.version_info.minor}"
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trustee-usb-") as tmp:
        package = Path(tmp) / label
        package.mkdir()
        for folder in ("src", "rules", "docs", "data/sample"):
            shutil.copytree(ROOT / folder, package / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("streamlit_app.py", "requirements.txt", "requirements-ui.txt", "constraints-ui.txt", "start-macos.command", "start-windows.cmd", "README.md", "LICENSE", "NOTICE"):
            shutil.copy2(ROOT / name, package / name)
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                        "-r", str(ROOT / "requirements-ui.txt"), "-c", str(ROOT / "constraints-ui.txt"),
                        "-d", str(package / "wheelhouse")], check=True)
        commit, dirty = git_state()
        (package / "BUILD.json").write_text(
            json.dumps({
                "platform": platform.platform(),
                "python": sys.version,
                "source": "git checkout" if commit else "ZIP source folder",
                "commit": commit,
                "working_tree_dirty": dirty,
            }, indent=2),
            encoding="utf-8",
        )
        hashes = {str(p.relative_to(package)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(package.rglob("*")) if p.is_file()}
        (package / "SHA256.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        archive = shutil.make_archive(str(destination / label), "zip", tmp, label)
        print(archive)


if __name__ == "__main__":
    main()

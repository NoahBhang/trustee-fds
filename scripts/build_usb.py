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


def main():
    label = f"trustee-fds-{platform.system()}-{platform.machine()}-py{sys.version_info.major}{sys.version_info.minor}"
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trustee-usb-") as tmp:
        package = Path(tmp) / label
        package.mkdir()
        for folder in ("src", "rules", "docs", "data/sample"):
            shutil.copytree(ROOT / folder, package / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("streamlit_app.py", "requirements.txt", "requirements-ui.txt", "start-macos.command", "start-windows.cmd", "README.md", "LICENSE", "NOTICE"):
            shutil.copy2(ROOT / name, package / name)
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                        "-r", str(ROOT / "requirements-ui.txt"), "-d", str(package / "wheelhouse")], check=True)
        (package / "BUILD.json").write_text(
            json.dumps({
                "platform": platform.platform(),
                "python": sys.version,
                "source": "ZIP source folder",
                "commit": None,
                "working_tree_dirty": None,
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

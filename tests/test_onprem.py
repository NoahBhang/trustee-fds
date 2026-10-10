"""로컬 실행기·배포 스크립트의 OS 경계 계약."""
import os
import re
import socket
import stat
import sys

import pytest

from src.onprem import ROOT, port_available

sys.path.insert(0, str(ROOT / "scripts"))
import build_usb  # noqa: E402


def test_port_in_use_is_reported_before_launch():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert not port_available(port)
    assert port_available(port)


def test_windows_launcher_uses_crlf():
    """cmd.exe는 LF 줄바꿈의 UTF-8 배치 파일을 잘못 읽을 수 있다 (.gitattributes 참조)."""
    content = (ROOT / "start-windows.cmd").read_bytes()
    assert content.endswith(b"\r\n")
    assert not re.search(rb"(?<!\r)\n", content)


@pytest.mark.skipif(os.name == "nt", reason="Windows에는 실행 권한 비트가 없다")
def test_macos_launcher_is_executable():
    assert (ROOT / "start-macos.command").stat().st_mode & stat.S_IXUSR


def test_build_records_git_commit():
    commit, dirty = build_usb.git_state()
    if commit is None:
        pytest.skip("git 저장소가 아닌 소스 묶음")
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert isinstance(dirty, bool)

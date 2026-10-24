"""pytest 공통 준비. 실행: .venv\\Scripts\\python -m pytest tests
- 설정 폴더(APPDATA)를 검사마다 새 임시 폴더로 바꿔 실제 설정·색인을 건드리지 않는다.
- 휴지통은 실제로 쓰지 않는다(fake_trash: send2trash 대신 임시 '휴지통' 폴더로 옮기고 경로를 기록한다)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 검사 전체의 바탕도 임시 폴더로 둔다. 검사마다 home이 다시 바꾸지만, monkeypatch를 잘못 되돌려도(undo) 실제 설정·색인·차단 목록에 닿지 않게.
SAFE = Path(tempfile.mkdtemp(prefix="file_organizer_tests_"))
os.environ["APPDATA"] = str(SAFE / "appdata")
os.environ["PROGRAMDATA"] = str(SAFE / "programdata")

import blacklist  # noqa: E402
import captures  # noqa: E402
import games  # noqa: E402
import index  # noqa: E402
import jobs  # noqa: E402
import lock  # noqa: E402
import server  # noqa: E402
import settings  # noqa: E402
import trash  # noqa: E402
import undo  # noqa: E402

DAY = 86400


@pytest.fixture(scope="session", autouse=True)
def clean_temp(tmp_path_factory):
    """검사가 모두 끝나면 지어낸 파일·가짜 휴지통이 든 임시 폴더를 지운다(pytest의 보관 설정만으로는 Windows에서 남았다)."""
    yield
    shutil.rmtree(tmp_path_factory.getbasetemp(), ignore_errors=True)
    shutil.rmtree(SAFE, ignore_errors=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """임시 설정 폴더와 색인할 폴더 하나(root). 놓을 곳은 비워 둔다.
    차단 목록 폴더(PROGRAMDATA)도 임시로 바꾸고, 그 폴더의 권한 맞추기(관리자만 쓰기)는 하지 않는다(하면 검사 뒤에 지울 수 없다)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    monkeypatch.setattr(blacklist, "restrict", lambda path: None)
    monkeypatch.setattr(games, "launcher_games", lambda: [])  # 이 PC의 Steam 등은 읽지 않는다.
    monkeypatch.setattr(captures, "folders", lambda: {"pictures": str(tmp_path / "사진"), "videos": str(tmp_path / "동영상"), "steam": str(tmp_path / "Steam")})
    undo.history.clear()  # 되돌리기 기록은 프로세스에 남으므로 검사마다 비운다.
    root = tmp_path / "root"
    root.mkdir()
    settings.save({"roots": [str(root)]})
    return root


def write(path, text="내용"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def build(root):
    index.stop.clear()
    index.build(index.roots(settings.load()))


def age(path, days):
    """연 시각과 수정 시각을 days일 전으로. 새 파일은 백신 검사가 연 시각을 바꾸므로 잠깐 기다린 뒤에 한다(tests.py의 settle)."""
    when = time.time() - days * DAY
    os.utime(path, (when, when))


@pytest.fixture
def fake_trash(tmp_path, monkeypatch):
    """send2trash 대신: 경로를 기록하고 임시 '휴지통' 폴더로 옮긴다. 이 동안 영구 삭제 함수가 불리면 검사가 실패한다."""
    bin_folder = tmp_path / "휴지통"
    bin_folder.mkdir()
    sent = []

    def send(path):
        sent.append(path)
        os.rename(path, bin_folder / f"{len(sent)}_{os.path.basename(path)}")

    monkeypatch.setattr(trash, "send", send)
    return sent


@pytest.fixture
def no_permanent_delete(monkeypatch):
    """영구 삭제 함수(os.remove, os.unlink, shutil.rmtree)가 불리면 곧바로 실패하게 한다."""
    def forbidden(*args, **kwargs):
        raise AssertionError(f"영구 삭제 함수가 불렸습니다: {args}")

    monkeypatch.setattr(os, "remove", forbidden)
    monkeypatch.setattr(os, "unlink", forbidden)
    monkeypatch.setattr(shutil, "rmtree", forbidden)


@pytest.fixture
def client():
    """열쇠 쿠키를 받은 테스트 클라이언트(창과 같다)."""
    c = server.app.test_client()
    c.get("/?key=" + server.KEY)
    return c


def finish():
    """뒤에서 도는 작업이 끝나기를 기다려 상태를 돌려준다."""
    status = jobs.wait(60)
    assert not status["running"], "작업이 끝나지 않았습니다"
    return status


@pytest.fixture
def unlock_after(tmp_path):
    """잠금 검사가 끝나면(실패해도) 임시 폴더 아래의 거부 권한을 모두 뗀다. 안 떼면 임시 폴더를 지울 수 없다."""
    yield
    subprocess.run(["icacls", str(tmp_path), "/remove:d", f"*{lock.user_sid()}", "/T", "/C"], capture_output=True)
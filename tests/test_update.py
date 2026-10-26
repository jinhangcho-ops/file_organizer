"""새 버전 알림과 [지금 업데이트]. 네트워크·창 없이 urlopen·Popen을 바꿔 끼운다. 설치 파일은 지어낸 바이트다."""
import hashlib
import io
import re
import subprocess
import urllib.error
from pathlib import Path

import pytest

import index
import jobs
import settings
import update

ROOT = Path(__file__).resolve().parent.parent
REPO = update.REPO
SETUP = b"MZ fake setup"


class Answer(io.BytesIO):
    def __init__(self, url, body):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()


@pytest.fixture
def net(monkeypatch, tmp_path):
    """routes: 주소 → (넘어간 주소, 내용), 예외, 또는 받는 동안 부를 함수. calls: 연 주소들. 설치한 exe(버전 1.9.0)처럼 꾸민다."""
    routes, calls = {}, []

    def urlopen(url, timeout):
        calls.append(url)
        found = routes.get(url, urllib.error.HTTPError(url, 404, "없음", None, None))
        if isinstance(found, Exception):
            raise found
        return Answer(*(found() if callable(found) else found))

    monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(update.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update.sys, "executable", str(tmp_path / "app" / "file_organizer.exe"))
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "version.ini").write_text("[app]\nversion=1.9.0\n", encoding="utf-8")
    monkeypatch.setattr(update, "asked", {})
    monkeypatch.setattr(update.tempfile, "tempdir", str(tmp_path))  # 받은 설치 파일을 실제 임시 폴더에 남기지 않게
    return routes, calls


def latest(routes, tag):
    routes[REPO + "/releases/latest"] = (f"{REPO}/releases/tag/{tag}", b"<html>")


def test_finds_newer_with_notes_once(net):
    routes, calls = net
    latest(routes, "v1.10.0")
    routes[f"{REPO}/releases/download/v1.10.0/RELEASE_NOTES.md"] = ("x", "### 추가\n- 새 기능\n".encode())
    found = update.check(True)
    assert found == {"current": "1.9.0", "version": "1.10.0", "notes": "### 추가\n- 새 기능\n"}  # 1.10 > 1.9(글자로 견주면 반대)
    assert update.check(True) == found and len(calls) == 2  # 실행마다 한 번만 묻는다


def test_newer_without_notes(net):
    routes, _ = net
    latest(routes, "v2.0.0")  # 노트는 404
    assert update.check(True) == {"current": "1.9.0", "version": "2.0.0", "notes": None}


@pytest.mark.parametrize("tag", ["v1.9.0", "v1.8.9", "nightly", "v1.10.0-beta", "v..", "x"])
def test_same_old_or_odd_tag_no_notice(net, tag):
    routes, calls = net
    latest(routes, tag)
    assert update.check(True) is None
    assert calls == [REPO + "/releases/latest"]  # 노트는 받지 않는다


def test_odd_path_tag(net):
    routes, calls = net
    routes[REPO + "/releases/latest"] = (f"{REPO}/releases/tag/v../x", b"")
    assert update.check(True) is None and len(calls) == 1


def test_off_or_offline_or_not_installed(net, monkeypatch, tmp_path):
    routes, calls = net
    assert update.check(False) is None and calls == []  # 끄면 아무 요청도 안 한다
    routes[REPO + "/releases/latest"] = urllib.error.URLError("프록시 오류")
    assert update.check(True) is None  # 조용히 넘어간다
    for version in ("0.0.0", "", "1.x"):  # 태그 없이 만든 시험 빌드, 버전 모름
        (tmp_path / "app" / "version.ini").write_text(f"[app]\nversion={version}\n", encoding="utf-8")
        monkeypatch.setattr(update, "asked", {})
        calls.clear()
        assert update.check(True) is None and calls == []
    monkeypatch.delattr(update.sys, "frozen")  # 개발 실행
    assert update.installed() is None


@pytest.fixture
def setup_files(net, monkeypatch):
    """v2.0.0의 설치 파일과 지문. started: 띄운 명령줄. code: 설치가 2초 안에 끝낼 때의 코드(None은 계속 돎)."""
    routes, calls = net
    base = f"{REPO}/releases/download/v2.0.0/{update.SETUP}"
    routes[base] = ("x", SETUP)
    routes[base + ".sha256"] = ("x", f"{hashlib.sha256(SETUP).hexdigest().upper()}  {update.SETUP}\n".encode())
    started, code = [], {"value": None}

    class Process:
        def __init__(self, args):
            started.append(args)

        def wait(self, timeout):
            if code["value"] is None:
                raise subprocess.TimeoutExpired("setup", timeout)
            return code["value"]

    monkeypatch.setattr(update.subprocess, "Popen", Process)
    monkeypatch.setattr(update.shutil, "disk_usage", lambda path: type("U", (), {"free": update.NEED})())
    monkeypatch.setattr(jobs, "closing", False)
    return routes, calls, base, started, code


def test_install_runs_exact_command(setup_files):
    routes, calls, base, started, _ = setup_files
    path = update.install("2.0.0")
    folder = str(Path(update.sys.executable).parent)
    assert started == [[path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", f"/DIR={folder}", "/RELAUNCH"]]
    assert Path(path).read_bytes() == SETUP and jobs.closing
    assert calls == [base, base + ".sha256"]  # 지정 버전의 릴리스 주소


@pytest.mark.parametrize("sums", [b"0" * 64 + b"  file_organizer_setup.exe\n", b"", b"   \n"])
def test_wrong_or_empty_fingerprint_not_run(setup_files, sums):
    routes, _, base, started, _ = setup_files
    routes[base + ".sha256"] = ("x", sums)
    with pytest.raises(ValueError, match="지문"):
        update.install("2.0.0")
    assert started == [] and jobs.closing is False


@pytest.mark.parametrize("version", ["../2.0.0", "2.0.0/x", "", None, "v2.0.0", "2.0.0 /VERYSILENT"])
def test_bad_version_not_downloaded(setup_files, version):
    _, calls, _, started, _ = setup_files
    with pytest.raises(ValueError, match="버전"):
        update.install(version)
    assert calls == [] and started == []


def test_quick_failure_keeps_app(setup_files):
    _, _, _, started, code = setup_files
    code["value"] = 5  # 관리자 확인 거절 등
    with pytest.raises(ValueError, match="코드 5"):
        update.install("2.0.0")
    assert len(started) == 1 and jobs.closing is False  # 다시 누를 수 있다


def test_refused_before_download(setup_files, monkeypatch):
    _, calls, _, started, _ = setup_files
    monkeypatch.setitem(jobs.status, "running", True)
    with pytest.raises(ValueError, match="작업"):
        update.install("2.0.0")
    monkeypatch.setitem(jobs.status, "running", False)
    monkeypatch.setitem(index.status, "running", True)
    with pytest.raises(ValueError, match="색인"):
        update.install("2.0.0")
    monkeypatch.setitem(index.status, "running", False)
    monkeypatch.setattr(update.shutil, "disk_usage", lambda path: type("U", (), {"free": update.NEED - 1})())
    with pytest.raises(ValueError, match="공간"):
        update.install("2.0.0")
    monkeypatch.delattr(update.sys, "frozen")
    with pytest.raises(ValueError, match="설치한 프로그램"):
        update.install("2.0.0")
    assert calls == [] and started == []


def test_setting_on_by_default_and_checked():
    assert settings.load()["update_check"] is True
    assert settings.save({"update_check": False})["update_check"] is False
    with pytest.raises(ValueError):
        settings.save({"update_check": "no"})


def test_changelog_sections():
    """모든 '## v' 절이 정확히 추가·개선·삭제 순서의 세 칸을 갖는다."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.split(r"^## (v\S+)\n", text, flags=re.M)[1:]
    assert sections
    for tag, body in zip(sections[::2], sections[1::2]):
        assert re.findall(r"^### (.+)$", body, flags=re.M) == ["추가", "개선", "삭제"], tag
        for part in re.split(r"^### .+\n", body, flags=re.M)[1:]:
            assert re.search(r"^- \S", part, flags=re.M), f"{tag}: 빈 칸은 '- 없음'으로"


def test_workflow_uses_changelog_notes():
    flow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "--generate-notes" not in flow
    assert "CHANGELOG.md" in flow and "RELEASE_NOTES.md" in flow and "--clobber" in flow
    assert '"/DAppVersion=$($env:GITHUB_REF_NAME.Substring(1))"' in flow and "windows-latest" not in flow
    assert flow.count("persist-credentials: false") == 2


def test_installer_records_version_and_relaunch():
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8-sig")
    assert 'Filename: "{app}\\version.ini"; Section: "app"; Key: "version"; String: "{#AppVersion}"' in iss
    assert "Check: Relaunch" in iss and "'/RELAUNCH'" in iss
    assert re.search(r'^Filename: .*Check: Relaunch$', iss, flags=re.M).group(0).count("skipifsilent") == 0


def test_redirect_elsewhere_is_not_a_tag(net):
    """와이파이 로그인 페이지·프록시가 다른 곳(…/v2)으로 넘기면 태그로 읽지 않는다."""
    routes, calls = net
    routes[REPO + "/releases/latest"] = ("http://login.example.invalid/portal/v2", b"<html>")
    assert update.check(True) is None and len(calls) == 1


def test_second_install_refused(setup_files):
    """[업데이트]를 두 번 눌러도 설치는 하나만 띄운다."""
    _, _, _, started, _ = setup_files
    update.install("2.0.0")
    with pytest.raises(ValueError, match="이미"):
        update.install("2.0.0")
    assert len(started) == 1


def test_no_new_job_while_updating(setup_files):
    """받는 동안(느린 인터넷이면 수십 초) 시작한 작업은 설치를 띄운 10초 뒤 강제 종료에 끊긴다: 업데이트를 시작한 순간부터 새 작업을 거절한다."""
    routes, _, base, _, _ = setup_files
    tried = []

    def during_download():
        with pytest.raises(ValueError, match="닫는 중"):
            jobs.start("다운로드 정리", lambda progress: None)
        tried.append(True)
        return ("x", SETUP)

    routes[base] = during_download
    update.install("2.0.0")
    assert tried == [True] and jobs.closing


def test_failed_update_allows_jobs_again(setup_files):
    routes, _, base, _, _ = setup_files
    routes[base + ".sha256"] = ("x", b"0" * 64)
    with pytest.raises(ValueError, match="지문"):
        update.install("2.0.0")
    assert jobs.closing is False  # 다시 작업하고 다시 누를 수 있다


def test_app_keeps_mutex_and_setup_waits():
    """앱은 끝날 때까지 뮤텍스를 쥐고(놓았다 쥐면 그사이 설치가 켜진 앱의 파일을 바꾼다), 설치가 InitializeSetup에서 기다린다.
    InitializeSetup은 AppMutex 확인보다 먼저 돈다(Inno Setup 6.7.3에서 시험 설치 파일로 확인)."""
    assert not hasattr(update, "release_mutex") and "release_mutex" not in (ROOT / "desktop.py").read_text(encoding="utf-8")
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8-sig")
    setup = iss[iss.index("function InitializeSetup"):]
    assert "if Relaunch then" in setup and "CheckForMutexes('file_organizer_app') and not ExeInUse" in setup and "Sleep(" in setup
    # v1.0.2 앱은 뮤텍스를 먼저 놓고 설치를 띄운다: 그 앱에서 올 때도 exe가 풀릴 때까지 기다린다(앱이 넘긴 /DIR의 exe를 쓰기로 열어 본다)
    assert "{param:DIR}" in iss and "'file_organizer.exe'" in iss and "CreateFileW@kernel32.dll stdcall" in iss

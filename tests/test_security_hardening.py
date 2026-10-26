"""보안 감사 묶음 A: 설치한 exe는 업데이트 받는 곳을 바꿀 수 없음, [열기]가 실행되는 파일을 실행하지 않음, 캐시 정리가 알려진 폴더를 캐시로 보지 않음,
새 비밀번호 8자·틀리면 기다리기, Referrer-Policy, 창이 앱 서버 밖으로 넘어가지 않음.
창을 띄우지 않는다(desktop.py는 불러오지 않고 함수만 떼어 돌린다). 열기·탐색기는 바꿔 끼워 실행하지 않는다."""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import cache
import lock
import server
from conftest import build, write
from test_blocked_and_locked_folders import PASSWORD
from test_lock_check_and_files import box  # noqa: F401  (잠근 폴더 준비)

ROOT = Path(__file__).resolve().parent.parent


def test_installed_exe_ignores_update_repo_env(tmp_path):
    """같은 계정의 스크립트가 환경 변수 하나로 [지금 업데이트]가 받을 곳을 바꾸지 못한다(소스 실행의 화면 확인만 바꾼다)."""
    script = "import sys; sys.frozen = {}; sys.path.insert(0, {!r}); import update; print(update.REPO)"
    env = {**os.environ, "APPDATA": str(tmp_path), "FILE_ORGANIZER_UPDATE_REPO": "http://updates.example.invalid"}
    for frozen, want in ((True, "https://github.com/jinhangcho-ops/file_organizer"), (False, "http://updates.example.invalid")):
        done = subprocess.run([sys.executable, "-c", script.format(frozen, str(ROOT))], capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        assert done.stdout.strip() == want, done.stderr


@pytest.fixture
def no_run(monkeypatch):
    """os.startfile(연결된 프로그램으로 열기)과 탐색기 띄우기를 적기만 한다."""
    started, revealed, real = [], [], subprocess.Popen

    def popen(args, *more, **options):  # 잠금의 icacls 등은 그대로 돌린다
        return revealed.append(args[-1]) if args[0] == "explorer" else real(args, *more, **options)

    monkeypatch.setattr(os, "startfile", started.append)
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    return started, revealed


def test_open_shows_runnable_files_in_explorer(home, client, no_run):
    started, revealed = no_run
    for name in ("견적서.pdf", "견적서.pdf.lnk", "SETUP.BAT", "사진.jpg.js"):
        write(home / name)
    (home / "자료.exe").mkdir()  # 이름이 .exe로 끝나는 폴더는 탐색기로 연다(실행되지 않는다)
    write(home / "자료.exe" / "a.txt")
    build(home)
    notes = {name: client.post("/api/open", json={"path": str(home / name)}).json["note"]
             for name in ("견적서.pdf", "견적서.pdf.lnk", "SETUP.BAT", "사진.jpg.js", "자료.exe")}
    assert started == [str(home / "견적서.pdf"), str(home / "자료.exe")]
    assert revealed == [str(home / n) for n in ("견적서.pdf.lnk", "SETUP.BAT", "사진.jpg.js")]
    assert notes["견적서.pdf"] == "" and "탐색기" in notes["SETUP.BAT"]
    assert client.post("/api/open", json={"path": str(home / "견적서.pdf"), "reveal": True}).json["note"] == ""  # [탐색기에서 보기]는 알리지 않는다


def test_lock_files_view_does_not_run_programs(box, client, no_run):  # noqa: F811
    path, _ = box
    started, revealed = no_run
    client.post("/api/lock/open", json={"path": path, "password": PASSWORD, "explorer": False})
    write(Path(path) / "풀기.cmd", "echo")
    assert "탐색기" in client.post("/api/lock/open_file", json={"path": os.path.join(path, "풀기.cmd")}).json["note"]
    assert client.post("/api/lock/open_file", json={"path": os.path.join(path, "일기.txt")}).json["note"] == ""
    assert started == [os.path.join(path, "일기.txt")] and revealed == [os.path.join(path, "풀기.cmd")]


def test_cache_never_treats_known_folders_as_cache(tmp_path, monkeypatch):
    """TEMP를 바탕 화면(이나 그 안·그것을 품은 곳)으로 바꿔 두면, 그 파일이 '오래된 캐시'로 휴지통에 가지 않게 뺀다."""
    known = {"다운로드": tmp_path / "받은 파일", "문서": tmp_path / "문서", "바탕 화면": tmp_path / "바탕"}
    for folder in (*known.values(), tmp_path / "바탕" / "temp", tmp_path / "temp"):
        folder.mkdir(exist_ok=True)
    monkeypatch.setattr(cache.drives, "known_folders", lambda: {name: str(p) for name, p in known.items()})
    for temp in (tmp_path / "바탕", tmp_path / "바탕" / "temp", tmp_path):
        monkeypatch.setenv("TEMP", str(temp))
        assert cache.folders([r"%TEMP%"]) == [], temp
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    assert cache.folders([r"%TEMP%"]) == [str(tmp_path / "temp")]  # 보통의 임시 폴더는 그대로


def test_new_password_needs_eight_and_old_short_one_still_works(home, monkeypatch):
    waited = []
    monkeypatch.setattr(lock.time, "sleep", waited.append)
    salt = "00" * 16
    lock.save({**lock.load(), "hash": lock.digest("abcd", salt), "salt": salt})  # 예전 판에서 정한 4자 비밀번호
    lock.verify("abcd")
    assert waited == []
    with pytest.raises(ValueError, match="8자"):
        lock.set_password("1234567", "abcd")
    with pytest.raises(ValueError, match="맞지 않습니다"):
        lock.verify("abce")
    assert waited == [lock.WRONG_WAIT]  # 틀리면 기다린다
    lock.set_password("12345678", "abcd")
    lock.verify("12345678")


def test_no_referrer(client):
    for url in ("/", "/lock", "/api/state", "/static/app.js"):
        assert client.get(url).headers["Referrer-Policy"] == "no-referrer", url


DESKTOP = (ROOT / "desktop.py").read_text(encoding="utf-8")


def desktop_functions(*names):
    tree = ast.parse(DESKTOP)
    space = {"ORIGIN": "http://127.0.0.1:40598/"}
    exec("\n".join(ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names), space)
    return space


class Handlers(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


class Args:
    def __init__(self, uri):
        self.Uri, self.Cancel, self.Response = uri, False, None
        self.Request = type("R", (), {"Uri": uri})()


class Core:
    """화면 엔진(CoreWebView2) 흉내: 요청 처리기와, 막을 때 만드는 응답."""
    def __init__(self):
        self.WebResourceRequested = Handlers()
        self.Environment = type("Env", (), {"CreateWebResourceResponse": staticmethod(lambda stream, code, reason, headers: code)})()


def test_window_stays_inside_the_app(monkeypatch):
    """창은 이 앱 서버 주소로만 가고 요청한다(다른 127.0.0.1 포트로 가면 열쇠 쿠키가 따라갔다: 실제 창에서 확인). 새 창 요청은 기본 브라우저로 넘기지 않는다."""
    space = desktop_functions("inside_app", "keep_inside")
    stay = ["http://127.0.0.1:40598/?key=x", "http://127.0.0.1:40598/lock?key=x", "http://127.0.0.1:40598/static/app.css"]
    leave = ["http://127.0.0.1:4059/", "http://127.0.0.1:405981/", "http://127.0.0.1:40598@example.invalid/", "https://example.invalid/",
             "file:///C:/Windows/System32/calc.exe", "about:blank", "javascript:alert(1)"]
    assert all(space["inside_app"](u) for u in stay) and not any(space["inside_app"](u) for u in leave)
    on_ui = []
    monkeypatch.setitem(sys.modules, "System", type("System", (), {"Action": staticmethod(lambda f: f)}))

    def view(core):
        control = type("W", (), {"NavigationStarting": Handlers(), "CoreWebView2InitializationCompleted": Handlers(), "CoreWebView2": core})()
        native = type("N", (), {"browser": type("B", (), {"webview": control})(), "Invoke": staticmethod(lambda f: on_ui.append(f) or f())})()
        events = type("E", (), {"before_show": Handlers()})()
        return type("V", (), {"events": events, "native": None})(), native, control

    main, main_native, main_control = view(None)  # webview.start 전에 만든 본 창: 창이 생길 때(before_show) 붙이고, 엔진은 준비되면
    space["keep_inside"](main)
    assert not main_control.NavigationStarting and len(main.events.before_show) == 1
    main.native = main_native
    main.events.before_show[0]()
    main_core = Core()
    main_control.CoreWebView2 = main_core
    main_control.CoreWebView2InitializationCompleted[0](main_control, None)
    lock_core = Core()
    lock, lock.native, lock_control = view(lock_core)  # start 뒤에 만든 잠금 창: 화면 스레드(Invoke)에서 바로 붙인다
    space["keep_inside"](lock)
    assert len(on_ui) == 1
    lock_control.CoreWebView2InitializationCompleted[0](lock_control, None)  # 준비 알림이 또 와도 두 번 붙이지 않는다
    for control, core in ((main_control, main_core), (lock_control, lock_core)):
        assert len(control.NavigationStarting) == 1 and len(core.WebResourceRequested) == 1
        moves = [Args(u) for u in stay + leave]
        for args in moves:
            control.NavigationStarting[0](None, args)
            core.WebResourceRequested[0](core, args)
        assert [a.Cancel for a in moves] == [False] * len(stay) + [True] * len(leave)
        assert [a.Response for a in moves] == [None] * len(stay) + [403] * len(leave)  # 밖으로 가는 요청은 네트워크로 보내지 않는다
    data = Args("data:image/png;base64,AAAA")
    main_core.WebResourceRequested[0](main_core, data)
    assert data.Response is None
    assert "EdgeChrome.on_new_window_request = lambda self, sender, args: args.set_Handled(True)" in DESKTOP
    import webview  # 바꿔 끼우는 처리기가 pywebview에 그 이름으로 있다(판을 올리면 여기서 알아챈다). 화면 엔진은 올리지 않고 글자만 본다.
    edge = (Path(webview.__file__).parent / "platforms" / "edgechromium.py").read_text(encoding="utf-8")
    assert "def on_new_window_request(self, sender, args):" in edge and "NewWindowRequested += self.on_new_window_request" in edge
    assert DESKTOP.count("keep_inside(window)") == 1 and DESKTOP.count("keep_inside(lock)") == 1

"""열 번째 감사(창 쪽): 되돌리지 못한 최소화 창은 안내를 띄우는지, 같은 제목의 다른 창 뒤의 본 창도 찾는지,
창을 닫은 뒤 남은 인식 프로세스를 끝내는지. 창을 띄우지 않는다(desktop.py는 불러오지 않고 함수만 떼어 돌린다)."""
import ast
import subprocess
import sys
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = (ROOT / "desktop.py").read_text(encoding="utf-8")
TREE = ast.parse(DESKTOP)
HEADER = "import ctypes\nimport os\nimport sys\nfrom ctypes import wintypes\n"


def function(name):
    return ast.unparse(next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == name))


class User32:
    """진짜 user32에 넘기되, 앞으로 가져오기·되돌리기·최소화 여부만 흉내 낸다(사용자 화면의 창을 건드리지 않는다)."""
    def __init__(self, iconic):
        self.iconic, self.shown = iconic, []

    def __getattr__(self, name):
        import ctypes
        return getattr(ctypes.windll.user32, name)

    def IsIconic(self, hwnd):
        return self.iconic

    def ShowWindow(self, hwnd, how):
        return 0  # 관리자 권한 창: 되돌리기가 거부된다(UIPI)

    def SetForegroundWindow(self, hwnd):
        self.shown.append(hwnd)
        return 1


def show_running(iconic, same=lambda hwnd: True):
    space = {}
    exec(HEADER + function("show_running"), space)
    user32 = User32(iconic)
    import ctypes
    space["ctypes"] = types.SimpleNamespace(windll=types.SimpleNamespace(user32=user32))
    space["same_program"] = same
    return space["show_running"], user32, ctypes


def make_window(title):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD] + [ctypes.c_int] * 4 + [wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    hwnd = user32.CreateWindowExW(0, "STATIC", title, 0, 0, 0, 10, 10, None, None, None, None)  # 숨긴 창(보이지 않는다)
    assert hwnd
    return hwnd


def test_minimized_window_that_stays_minimized_shows_the_message():
    title = "file_organizer_test_" + uuid.uuid4().hex
    hwnd = make_window(title)
    try:
        run, user32, ctypes = show_running(iconic=1)
        assert run(title) is False and user32.shown == [hwnd]  # 앞으로는 왔지만 최소화된 채: 안내를 띄우게 False
        run, user32, ctypes = show_running(iconic=0)
        assert run(title) is True and user32.shown == [hwnd]
    finally:
        ctypes.windll.user32.DestroyWindow(hwnd)


def test_skips_a_window_of_another_program_with_the_same_title():
    """FindWindowW가 먼저 주는 창(다른 프로그램이라고 친다)을 건너뛰고 뒤의 본 창을 앞으로 가져온다."""
    title = "file_organizer_test_" + uuid.uuid4().hex
    windows = [make_window(title), make_window(title)]
    import ctypes
    from ctypes import wintypes
    real = ctypes.windll.user32
    real.FindWindowW.restype = wintypes.HWND
    real.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    try:
        first = real.FindWindowW(None, title)
        target = next(h for h in windows if h != first)
        asked = []
        run, user32, _ = show_running(iconic=0, same=lambda h: asked.append(h) or h == target)
        assert run(title) is True and user32.shown == [target] and asked == [first, target]
        run, user32, _ = show_running(iconic=0, same=lambda h: False)  # 모두 다른 프로그램
        assert run(title) is False and user32.shown == []
    finally:
        for hwnd in windows:
            real.DestroyWindow(hwnd)


def test_recognize_end_kills_only_the_running_recognizer(monkeypatch):
    """recognize.run이 띄운 프로세스(running)만 끝낸다. 이미 끝났거나 띄운 적이 없으면 아무것도 하지 않는다."""
    import recognize
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        monkeypatch.setattr(recognize, "running", [child])
        recognize.end()
        assert child.wait(10) is not None
        recognize.end()  # 끝난 것을 다시: 오류 없이 넘어간다
        monkeypatch.setattr(recognize, "running", [None])
        recognize.end()
    finally:
        child.kill()
        child.wait()


def test_closing_waits_then_ends_the_recognizer():
    tail = DESKTOP[DESKTOP.index("index.finish()"):]
    assert tail.index("index.worker.join(RECOGNIZE_LIMIT)") < tail.index("recognize.end()") < tail.index("lock.close_all()")
    assert "if index.worker is not None and index.worker.is_alive():" in tail

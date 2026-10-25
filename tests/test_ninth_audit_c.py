"""아홉 번째 감사(창·작업 쪽): 다른 권한으로 켠 프로그램도 알아보는지, 창을 닫은 뒤 새 작업을 받지 않는지,
둘째 실행이 켜져 있는 창을 앞으로 가져오는지, 끊긴 모델 받기의 임시 파일을 치우는지.
창을 띄우지 않는다(desktop.py는 불러오지 않고 글자·구문만 보거나 함수만 떼어 돌린다). 모델은 받지 않는다."""
import ast
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import jobs
import models

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = (ROOT / "desktop.py").read_text(encoding="utf-8")
TREE = ast.parse(DESKTOP)
HEADER = "import ctypes\nimport os\nimport sys\nfrom ctypes import wintypes\nMUTEX = ''\n"


def function(name):
    return ast.unparse(next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == name))


def run(script):
    """따로 띄운 파이썬에서 돌린다(뮤텍스는 그 프로세스가 끝나면 풀린다)."""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


# 뮤텍스를 쥔 채 자식 프로세스에서 child를 돌리고, 자식의 출력을 그대로 내보낸다.
HOLD = """
import subprocess, sys
{make}
print(subprocess.run([sys.executable, "-c", {child!r}], capture_output=True, text=True).stdout.strip())
"""


def test_mutex_of_another_privilege_counts_as_running():
    """관리자 권한 프로그램의 뮤텍스를 SYSTEM만 열 수 있는 뮤텍스로 흉내 낸다: CreateMutexW가 5(거부)를 주어도 켜져 있다고 본다."""
    name = "file_organizer_test_" + uuid.uuid4().hex
    make = HEADER + f"""
class SA(ctypes.Structure):
    _fields_ = [("n", wintypes.DWORD), ("sd", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
sd = ctypes.c_void_p()
assert ctypes.WinDLL("advapi32").ConvertStringSecurityDescriptorToSecurityDescriptorW("D:(A;;GA;;;SY)", 1, ctypes.byref(sd), None)
k = ctypes.WinDLL("kernel32")
k.CreateMutexW.restype = wintypes.HANDLE
k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
assert k.CreateMutexW(ctypes.byref(SA(ctypes.sizeof(SA), sd, False)), False, {name!r})
"""
    child = HEADER + function("already_running") + f"\nprint(already_running({name!r}))"
    assert run(HOLD.format(make=make, child=child)) == ["True"]


def test_mutex_lets_everyone_wait_but_not_more():
    """already_running이 만든 뮤텍스를 다른 프로세스가 SYNCHRONIZE로 열 수 있고(설치의 AppMutex 확인), 전체 권한은 못 받는다(설명자가 걸렸다)."""
    name = "file_organizer_test_" + uuid.uuid4().hex
    make = HEADER + function("already_running") + f"\nassert already_running({name!r}) is False"
    child = HEADER + function("already_running") + f"""
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.OpenMutexW.restype = wintypes.HANDLE
k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
print(bool(k.OpenMutexW(0x00100000, False, {name!r})), bool(k.OpenMutexW(0x001F0001, False, {name!r})), ctypes.get_last_error(), already_running({name!r}))
"""
    assert run(HOLD.format(make=make, child=child)) == ["True", "False", "5", "True"]
    assert run(HEADER + function("already_running") + f"\nprint(already_running({name!r}), already_running({name!r}))") == ["False", "True"]


def test_jobs_refuse_to_start_after_closing(monkeypatch):
    monkeypatch.setattr(jobs, "closing", True)
    before = dict(jobs.status)
    with pytest.raises(ValueError, match="프로그램을 닫는 중이라 작업을 시작하지 않았습니다."):
        jobs.start("옮기기", lambda progress: None)
    assert jobs.status == before


def test_desktop_sets_closing_before_checking_the_job():
    start, flag, check = DESKTOP.index("webview.start("), DESKTOP.index("    jobs.closing = True"), DESKTOP.index('if jobs.status["changes"]:')
    assert start < flag < check
    assert DESKTOP[:flag].rstrip().endswith("작업은 아래 확인이 못 보고, 프로세스와 함께 중간에 끊겼다.") and "with jobs.lock:" in DESKTOP[start:flag]


def test_second_launch_shows_the_open_window_first():
    branch = DESKTOP[DESKTOP.index("if already_running():"):DESKTOP.index("import webview")]
    assert branch.index("if not show_running():") < branch.index("MessageBoxW") < branch.index("sys.exit(0)")
    assert 'webview.create_window("파일 정리"' in DESKTOP and 'def show_running(title="파일 정리")' in DESKTOP  # 찾는 제목 = 본 창 제목


def test_show_running_only_touches_this_program():
    """같은 실행 파일의 창만 앞으로 가져온다. 창은 이 프로세스에 숨긴 채 만들어 본다(보이지 않는다)."""
    space = {}
    exec(HEADER + function("show_running") + "\n" + function("same_program"), space)
    assert space["show_running"]("file_organizer_test_" + uuid.uuid4().hex) is False  # 창이 없다
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD] + [ctypes.c_int] * 4 + [wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    hwnd = user32.CreateWindowExW(0, "STATIC", "file_organizer_test_" + uuid.uuid4().hex, 0, 0, 0, 10, 10, None, None, None, None)
    assert hwnd
    try:
        assert space["same_program"](hwnd) is True
    finally:
        user32.DestroyWindow(wintypes.HWND(hwnd))
    user32.FindWindowW.restype = wintypes.HWND
    tray = user32.FindWindowW("Shell_TrayWnd", None)  # 탐색기의 창: 제목이 같아도 다른 프로그램이다
    if tray:
        assert space["same_program"](tray) is False


def test_download_removes_left_part_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_ORGANIZER_MODELS", str(tmp_path))
    monkeypatch.setattr(models, "fetch", lambda *args: pytest.fail("받지 않는다"))
    left = [tmp_path / "clip" / "vision.onnx.part", tmp_path / "face" / "sface.onnx.part"]
    keep = [tmp_path / "clip" / "mine.part", tmp_path / "clip" / "vision.onnx"]  # 앱이 붙인 이름이 아니거나 임시 파일이 아닌 것
    for file in left + keep:
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(b"x")
    assert models.download([]) == {"done": [], "failed": []}
    assert [f.exists() for f in left + keep] == [False, False, True, True]

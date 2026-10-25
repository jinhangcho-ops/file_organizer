"""여덟 번째 감사(창·작업 쪽): 창을 닫으면 잠금 폴더부터 다시 잠그는지, 파일을 바꾸는 작업만 기다리는지, 한 번에 하나만 켜지는지,
잠금 창 코드가 opening 안에서 창을 기다리지 않는지. 창을 띄우지 않는다(desktop.py는 불러오지 않고 글자·구문만 본다)."""
import ast
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import jobs

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = (ROOT / "desktop.py").read_text(encoding="utf-8")
TREE = ast.parse(DESKTOP)


def function(name, tree=TREE):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def calls(node):
    """node 안에서 부르는 이름들(a.b.c(...)의 c)."""
    return {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(node) if isinstance(n, ast.Call)}


def test_locks_are_closed_before_waiting_for_the_job():
    start = DESKTOP.index("webview.start(")
    close, waits = DESKTOP.index("lock.close_all()", start), DESKTOP.index("jobs.wait(JOB_LIMIT)")
    assert start < close < waits


def test_only_jobs_that_change_files_are_waited_for():
    names = set(re.findall(r'jobs\.start\("([^"]+)"', (ROOT / "server.py").read_text(encoding="utf-8")))
    assert jobs.CUTTABLE <= names and len(names - jobs.CUTTABLE) == 8
    for name, changes in (("캐시 살펴보기", False), ("옮기기", True)):
        gate = threading.Event()
        jobs.start(name, lambda progress: gate.wait(5))
        try:
            assert jobs.status["running"] and jobs.status["changes"] is changes
        finally:
            gate.set()
            jobs.wait(10)
    assert 'if jobs.status["changes"]:\n    jobs.wait(JOB_LIMIT)' in DESKTOP
    assert "job.changes" in (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def test_mutex_says_whether_another_one_runs():
    """already_running만 떼어 다른 프로세스에서 두 번 부른다. 프로세스가 끝나면 풀려 다음 실행은 처음이 된다."""
    source = "import ctypes\nfrom ctypes import wintypes\nMUTEX = ''\n" + ast.unparse(function("already_running"))
    name = "file_organizer_test_" + uuid.uuid4().hex
    script = source + f"\nprint(already_running({name!r}), already_running({name!r}))"
    run = lambda: subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=60).stdout.split()
    assert run() == ["False", "True"]
    assert run() == ["False", "True"]
    assert "AppMutex=file_organizer_app" in (ROOT / "installer.iss").read_text(encoding="utf-8-sig")
    assert 'MUTEX = "file_organizer_app"' in DESKTOP
    assert DESKTOP.index('if "--recognize" in sys.argv') < DESKTOP.index("if already_running():") < DESKTOP.index("import webview")


def test_lock_window_waits_on_nothing_inside_opening():
    """opening 안에서 evaluate_js·restore를 부르지 않는다(닫히는 창에서 끝없이 기다려 잠금 창을 다시 못 열었다)."""
    opening = next(n for n in ast.walk(function("open_lock")) if isinstance(n, ast.With))
    assert not calls(opening) & {"evaluate_js", "restore", "SetForegroundWindow", "destroy"}  # 읽는 중인 창도 버리지 않는다
    assert "evaluate_js" in calls(function("open_lock"))
    end = function("end")
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "evaluate_js" for n in ast.walk(end))
    assert "Thread" in calls(end)


def test_revealed_only_after_show_succeeds():
    reveal = ast.unparse(function("reveal"))
    show, revealed = reveal.index("lock.show()"), reveal.index("lock.revealed = True")
    assert show < revealed and "except Exception" in reveal[show:revealed] and "잠금 창을 열지 못했어요" in reveal[show:revealed]

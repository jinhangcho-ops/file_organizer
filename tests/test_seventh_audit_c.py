"""일곱 번째 감사(창·화면 쪽): 창을 닫아도 돌던 작업은 끝까지 하는지(jobs.wait), 화면 HTML에 CSP가 막는 style 속성이 없는지,
바깥 파일 끌어 놓기 막기가 두 창이 같이 쓰는 common.js에 있는지. 창을 띄우지 않는다(desktop.py는 불러오지 않고 글자만 본다)."""
import re
import threading
from pathlib import Path

import jobs

ROOT = Path(__file__).resolve().parent.parent


def test_wait_lets_a_running_job_finish():
    gate = threading.Event()

    def work(progress):
        gate.wait(5)
        return "끝"

    jobs.start("시험", work)
    threading.Timer(0.3, gate.set).start()
    status = jobs.wait(10)
    assert not status["running"] and status["last"]["result"] == "끝"


def test_wait_gives_up_after_timeout():
    gate = threading.Event()
    jobs.start("시험", lambda progress: gate.wait(5))
    try:
        assert jobs.wait(0.2)["running"]
    finally:
        gate.set()
        jobs.wait(10)


def test_desktop_waits_for_the_job_before_closing():
    source = (ROOT / "desktop.py").read_text(encoding="utf-8")
    start, waits, finish = source.index("webview.start("), source.index("jobs.wait(JOB_LIMIT)"), source.index("index.finish()")
    assert start < waits < finish


def test_pages_have_no_inline_style():
    for name in ("index.html", "lock.html"):
        assert not re.search(r"\sstyle=", (ROOT / "static" / name).read_text(encoding="utf-8")), name


def test_outside_drop_is_blocked_in_both_windows():
    common = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("drop", (e) => e.preventDefault());' in common
    assert all('src="/static/common.js"' in (ROOT / "static" / page).read_text(encoding="utf-8") for page in ("index.html", "lock.html"))

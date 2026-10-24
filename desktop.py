"""프로그램 창을 연다. exe의 실행 파일이자 유일한 실행 방법."""
import ctypes
import os
import sys
import threading
from ctypes import wintypes

if "--recognize" in sys.argv:  # 사진·동영상 인식 프로세스: 창 없이 인식만 하고 끝난다(recognize.run이 띄운다).
    import recognize

    recognize.child_main()
    sys.exit(0)

import webview  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import index  # noqa: E402
import lock  # noqa: E402
import server  # noqa: E402
import settings  # noqa: E402

http = make_server("127.0.0.1", 0, server.app, threaded=True)  # 0: 비어 있는 포트를 자동으로 고른다.
threading.Thread(target=http.serve_forever, daemon=True).start()
# 열쇠를 아는 이 창만 화면을 열 수 있다(브라우저로 주소만 열면 거부된다).
# 제목 줄은 화면(index.html)이 그린다. 끌기는 pywebview의 drag region, 버튼과 가장자리 크기 조절은 /api/window → control.
BACKGROUND = {True: "#0F151B", False: "#E9ECE7"}  # 화면이 뜨기 전 창의 바탕(app.css의 --bg, 다크·라이트)
MIN_SIZE = (1160, 660)  # 창의 가장 작은 크기. 가장자리 끌기(화면)도 bounds로 받아 같은 값으로 막는다.
window = webview.create_window("파일 정리", f"http://127.0.0.1:{http.server_port}/?key={server.KEY}", width=1400, height=900,
                               frameless=True, easy_drag=False, min_size=MIN_SIZE, background_color=BACKGROUND[settings.dark()])
maximized = [None]  # 최대화했을 때 되돌아갈 창의 실제 픽셀 사각형(RECT). 최대화가 아니면 None.
user32 = ctypes.windll.user32
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
EXCLUDE_FROM_CAPTURE = 0x11  # WDA_EXCLUDEFROMCAPTURE: 화면 캡처·공유·녹화에서 이 창을 뺀다(Windows 10 2004 이상).
lock_window = [None]  # 열려 있는 잠금 창


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def place_rect(hwnd, rect):
    user32.SetWindowPos(hwnd, None, rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top, 0x0044)  # 순서 그대로, 보이기


def maximize():
    """테두리 없는 창을 Windows 방식으로 최대화하면 작업 표시줄까지 덮는다. 그래서 창이 있는 모니터의 작업 영역에 직접 맞춘다.
    되돌릴 자리는 실제 픽셀로 기억한다(논리 픽셀로 바꿨다 되돌리면 1px씩 어긋난다)."""
    hwnd = wintypes.HWND(window.native.Handle.ToInt64())
    before = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(before))
    maximized[0] = before
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))  # 2: 가장 가까운 모니터
    place_rect(hwnd, info.rcWork)  # 실제 픽셀


def control(action, data):
    """화면의 제목 줄 버튼과 가장자리 끌기. 창은 테두리가 없어 Windows가 크기 조절을 해 주지 않는다."""
    if action == "minimize":
        window.minimize()
    elif action == "toggle":
        if maximized[0]:
            place_rect(wintypes.HWND(window.native.Handle.ToInt64()), maximized[0])
            maximized[0] = None
        else:
            maximize()
        return {"maximized": bool(maximized[0])}
    elif action == "close":
        close_lock()
        window.destroy()
    elif action == "lock":
        open_lock(data.get("path"))
    elif action == "refresh":
        window.evaluate_js("refreshAfterLock()")
    elif action == "bounds":
        return {"x": window.x, "y": window.y, "width": window.width, "height": window.height, "maximized": bool(maximized[0]), "min": MIN_SIZE}
    elif action == "place":
        width, height = max(int(data["width"]), MIN_SIZE[0]), max(int(data["height"]), MIN_SIZE[1])
        window.move(int(data["x"]), int(data["y"]))
        window.resize(width, height)


def open_lock(path):
    """잠금 창(잠금 관련 화면은 모두 여기)을 연다. path: 다룰 폴더(잠금 창이 /api/lock/state로 가져간다). 이미 열려 있으면 앞으로 가져와 다시 읽는다.
    나타나기 전에 캡처에서 빼므로, 창 하나 공유든 화면 전체 공유든 이 창은 나가지 않는다(사용자 화면에는 보인다)."""
    server.lock_target = path if isinstance(path, str) else ""
    if lock_window[0] is not None:
        lock_window[0].restore()
        lock_window[0].evaluate_js("load()")
        return
    url = f"http://127.0.0.1:{http.server_port}/lock?key={server.KEY}"
    lock = webview.create_window("잠금", url, width=760, height=640, min_size=(560, 480), background_color=BACKGROUND[settings.dark()])

    def hide_from_capture():
        """보이기 전과 보인 뒤에 모두 건다(WinForms가 처음 보일 때 창 핸들을 다시 만들면 앞의 설정이 없어진다).
        걸리지 않으면(Windows 10 2004보다 오래된 Windows 등) 잠금 창을 닫는다: 공유 화면에 나가는 것보다 안 여는 쪽이 낫다."""
        hwnd = wintypes.HWND(lock.native.Handle.ToInt64())
        user32.SetWindowDisplayAffinity(hwnd, EXCLUDE_FROM_CAPTURE)
        now = wintypes.DWORD()
        user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(now))
        if now.value != EXCLUDE_FROM_CAPTURE:
            lock.destroy()
            window.evaluate_js("toast('이 Windows에서는 잠금 창을 화면 공유에서 뺄 수 없어 열지 않았어요(Windows 10 2004 이상 필요).')")

    lock.events.before_show += hide_from_capture
    lock.events.shown += hide_from_capture
    lock.events.closed += lambda: lock_window.__setitem__(0, None)
    lock_window[0] = lock


def close_lock():
    if lock_window[0] is not None:
        lock_window[0].destroy()


server.window_control = control
window.events.closed += close_lock  # 본 창을 닫으면 잠금 창도 닫는다.
lock.close_all()  # 지난번에 창을 정상으로 닫지 못했으면(끄기·강제 종료) 열어 둔 잠금 폴더가 남아 있다.
# 아이콘: 소스로 실행할 때는 icon.ico, exe에서는(파일이 없으므로) exe에 넣은 아이콘을 pywebview가 쓴다.
webview.start(icon=os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"))  # 창을 닫으면 여기서 끝나고, 서버 스레드도 함께 끝난다.
index.finish()  # 색인 중이었으면 지금 파일까지 저장하고 멈춘다(다음에 남은 것부터 이어서 한다).
lock.close_all()  # 열어 둔 잠금 폴더를 다시 잠근다.

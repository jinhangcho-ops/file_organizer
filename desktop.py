"""프로그램 창을 연다. exe의 실행 파일이자 유일한 실행 방법."""
import ctypes
import threading
from ctypes import wintypes

import webview
from werkzeug.serving import make_server

import index
import server

http = make_server("127.0.0.1", 0, server.app, threaded=True)  # 0: 비어 있는 포트를 자동으로 고른다.
threading.Thread(target=http.serve_forever, daemon=True).start()
# 열쇠를 아는 이 창만 화면을 열 수 있다(브라우저로 주소만 열면 거부된다).
# 제목 줄은 화면(index.html)이 그린다. 끌기는 pywebview의 drag region, 버튼과 가장자리 크기 조절은 /api/window → control.
window = webview.create_window("파일 정리", f"http://127.0.0.1:{http.server_port}/?key={server.KEY}", width=1400, height=900,
                               frameless=True, easy_drag=False, min_size=(1160, 660))
maximized = [None]  # 최대화했을 때 되돌아갈 창의 실제 픽셀 사각형(RECT). 최대화가 아니면 None.
user32 = ctypes.windll.user32
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]


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
        window.destroy()
    elif action == "bounds":
        return {"x": window.x, "y": window.y, "width": window.width, "height": window.height, "maximized": bool(maximized[0])}
    elif action == "place":
        width, height = max(int(data["width"]), 1160), max(int(data["height"]), 660)
        window.move(int(data["x"]), int(data["y"]))
        window.resize(width, height)


def ask_folder():
    """폴더 고르는 대화상자. 고른 경로 또는 취소하면 None."""
    chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
    return chosen[0] if chosen else None


server.ask_folder = ask_folder
server.window_control = control
webview.start()  # 창을 닫으면 여기서 끝나고, 서버 스레드도 함께 끝난다.
index.finish()  # 색인 중이었으면 지금 파일까지 저장하고 멈춘다(다음에 남은 것부터 이어서 한다).

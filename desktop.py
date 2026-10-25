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

MUTEX = "file_organizer_app"  # installer.iss의 AppMutex와 같은 이름(설치·업데이트가 켜진 프로그램을 알아본다)


def already_running(name=MUTEX):
    """이 이름의 뮤텍스가 이미 있으면 True. 핸들은 닫지 않는다: 프로세스가 끝날 때까지 쥐고 있어야 다음 실행이 알아본다."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW(None, False, name)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


# 둘째 프로세스는 같은 색인·설정을 함께 쓰고, 돌던 작업과 되돌리기 기록을 모른다(창을 닫고 작업을 마치는 중인 프로세스도 있다).
if already_running():
    ctypes.windll.user32.MessageBoxW(None, "파일 정리가 이미 켜져 있거나, 앞서 닫은 작업이 아직 끝나는 중이에요. 잠시 뒤에 다시 켜 주세요.", "파일 정리", 0x40)
    sys.exit(0)

import webview  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import index  # noqa: E402
import jobs  # noqa: E402
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
lock_window = [None]  # 열려 있는 잠금 창(revealed: 캡처 제외를 확인하고 보였는지)
opening = threading.Lock()  # 잠금 창을 만들고 보이는 동안
JOB_LIMIT = 6 * 3600  # 초. 창을 닫은 뒤 돌던 작업을 기다리는 가장 긴 시간(멈춘 작업 때문에 프로그램이 영영 남지 않게)
REVEAL_LIMIT = 20  # 초. 이때까지 화면을 못 읽으면 숨은 잠금 창을 치운다.


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
    elif action == "lock_reload":  # 본 창에서 화면 색을 바꿨다
        if lock_window[0] is not None and lock_window[0].revealed:  # 아직 숨은 창은 화면을 읽으면서 색을 맞춘다
            lock_window[0].evaluate_js("load()")
    elif action == "bounds":
        return {"x": window.x, "y": window.y, "width": window.width, "height": window.height, "maximized": bool(maximized[0]), "min": MIN_SIZE}
    elif action == "place":
        width, height = max(int(data["width"]), MIN_SIZE[0]), max(int(data["height"]), MIN_SIZE[1])
        window.move(int(data["x"]), int(data["y"]))
        window.resize(width, height)


def open_lock(path):
    """잠금 창(잠금 관련 화면은 모두 여기)을 연다. path: 다룰 폴더(잠금 창이 /api/lock/state로 가져간다). 이미 열려 있으면 앞으로 가져와 다시 읽는다.
    나타나기 전에 캡처에서 빼므로, 창 하나 공유든 화면 전체 공유든 이 창은 나가지 않는다(사용자 화면에는 보인다)."""
    with opening:  # 서버는 요청마다 스레드라 빠른 두 번의 [잠금 창 열기]가 둘 다 None을 보고 창을 둘 만들었다.
        server.lock_target = path if isinstance(path, str) else ""
        old = lock_window[0]
        if old is None:
            return create_lock()
        if not old.revealed:  # 아직 읽는 중인 창: 버리지 않는다(reveal이 보이거나 give_up이 REVEAL_LIMIT 뒤에 치운다).
            return
    # 잠금 밖에서: evaluate_js는 닫히는 창에서 끝없이 기다릴 수 있어, 잠금 안에서 부르면 잠금 창을 다시는 못 열었다.
    hwnd = wintypes.HWND(old.native.Handle.ToInt64())
    if user32.IsIconic(hwnd):  # 최소화했을 때만 되돌린다(restore는 최대화도 푼다)
        old.restore()
    user32.SetForegroundWindow(hwnd)  # 뒤에 있으면 앞으로
    old.evaluate_js("load()")


def create_lock():
    url = f"http://127.0.0.1:{http.server_port}/lock?key={server.KEY}"
    # 숨긴 채 만든다(pywebview는 투명하게 한 번 보였다 숨겨 창 핸들을 만든다). pywebview의 창 이벤트는 따로 도는 스레드라
    # before_show에서 걸면 실제로 보이는 것과 앞뒤가 엇갈려, 보이는 창에 캡처 제외가 빠진 적이 있다(10번에 1번쯤).
    # WinForms의 Shown은 그 투명한 Show 때 한 번만 와서, 실제로 보일 때의 확인은 reveal이 show() 바로 뒤에 한다.
    lock = webview.create_window("잠금", url, width=760, height=640, min_size=(560, 480), background_color=BACKGROUND[settings.dark()], hidden=True)
    lock.revealed = False
    ended = []  # refuse·give_up이 한 번만(알림·닫기가 두 번 되지 않게)

    def excluded():
        """지금 창 핸들에 캡처 제외를 걸고 걸렸는지."""
        hwnd = wintypes.HWND(lock.native.Handle.ToInt64())
        user32.SetWindowDisplayAffinity(hwnd, EXCLUDE_FROM_CAPTURE)
        now = wintypes.DWORD()
        user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(now))
        return now.value == EXCLUDE_FROM_CAPTURE

    def end(message):
        """잠금 창을 닫고 본 창에 알린다(한 번만)."""
        if ended:
            return
        ended.append(True)
        if lock_window[0] is lock:
            lock_window[0] = None
        lock.destroy()
        # 알림은 따로: end는 opening 안에서 불리고, evaluate_js는 본 창이 닫히는 중이면 끝없이 기다린다.
        threading.Thread(target=window.evaluate_js, args=(f"toast({message!r})",), daemon=True).start()

    def refuse():
        """걸리지 않으면(Windows 10 2004보다 오래된 Windows 등) 잠금 창을 닫는다: 공유 화면에 나가는 것보다 안 여는 쪽이 낫다."""
        end("이 Windows에서는 잠금 창을 화면 공유에서 뺄 수 없어 열지 않았어요(Windows 10 2004 이상 필요).")

    def reveal():
        """화면을 다 읽은 뒤(핸들이 만들어진 뒤) 캡처 제외를 확인하고서야 보인다. 보인 바로 뒤에도 다시 확인한다(핸들이 바뀌었으면)."""
        with opening:  # give_up·open_lock과 엇갈리지 않게
            if ended:
                return
            if not excluded():
                return refuse()
            if not lock.revealed:
                try:
                    lock.show()
                except Exception:  # 보이기가 20초를 넘었거나(WebViewException) Invoke가 실패: 보인 셈 치면 다시 열 수 없었다.
                    return end("잠금 창을 열지 못했어요. 다시 [잠금 창 열기]를 눌러 보세요.")
                lock.revealed = True  # 보인 뒤에만(그 전에 open_lock이 와도 읽는 중으로 보고 기다린다)
                if not excluded():
                    refuse()

    def give_up():
        """화면을 끝내 읽지 못하면 loaded가 오지 않는다: 숨은 창을 치우고 알린다(그대로 두면 다시 열 수 없었다)."""
        with opening:
            if not lock.revealed:
                end("잠금 창을 열지 못했어요. 다시 [잠금 창 열기]를 눌러 보세요.")

    def closed():
        ended.append(True)  # 닫힌 뒤에 give_up이 알리지 않게
        if lock_window[0] is lock:  # 버리고 새로 만든 창이면 새 창을 잊지 않는다
            lock_window[0] = None

    lock.events.loaded += reveal
    lock.events.closed += closed
    lock_window[0] = lock
    timer = threading.Timer(REVEAL_LIMIT, give_up)
    timer.daemon = True  # 프로그램을 닫을 때 기다리지 않게
    timer.start()


def close_lock():
    if lock_window[0] is not None:
        lock_window[0].destroy()


server.window_control = control
window.events.closed += close_lock  # 본 창을 닫으면 잠금 창도 닫는다.
lock.close_all()  # 지난번에 창을 정상으로 닫지 못했으면(끄기·강제 종료) 열어 둔 잠금 폴더가 남아 있다.
# 아이콘: 소스로 실행할 때는 icon.ico, exe에서는(파일이 없으므로) exe에 넣은 아이콘을 pywebview가 쓴다.
webview.start(icon=os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"))  # 창을 닫으면 여기서 끝나고, 서버 스레드도 함께 끝난다.
lock.close_all()  # 열어 둔 잠금 폴더를 바로 다시 잠근다(작업은 fence로 잠금 폴더를 피하고, lock.guard가 moved와 차례를 지킨다).
# 파일을 바꾸는 작업(옮기기·보관 등)은 창이 닫혀도 끝까지 한다: 작업 스레드는 daemon이라 그냥 끝내면 드라이브를 건너는 폴더 옮기기가 중간에 끊긴다.
# 읽기·받기만 하는 작업(jobs.CUTTABLE)은 끊는다. 화면의 [✕]는 묻고 닫는다(app.js). Alt+F4·작업 표시줄로 닫아도 여기서 기다린다.
if jobs.status["changes"]:
    jobs.wait(JOB_LIMIT)
index.finish()  # 색인 중이었으면 지금 파일까지 저장하고 멈춘다(다음에 남은 것부터 이어서 한다).
lock.close_all()  # 작업 중에 다시 연 것은 없지만(창이 없다) 한 번 더 해도 해가 없다.

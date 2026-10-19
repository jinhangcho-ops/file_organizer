"""프로그램 창을 연다. exe의 실행 파일이자 유일한 실행 방법."""
import threading

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
maximized = [False]


def control(action, data):
    """화면의 제목 줄 버튼과 가장자리 끌기. 창은 테두리가 없어 Windows가 크기 조절을 해 주지 않는다."""
    if action == "minimize":
        window.minimize()
    elif action == "toggle":
        window.restore() if maximized[0] else window.maximize()
        maximized[0] = not maximized[0]
        return {"maximized": maximized[0]}
    elif action == "close":
        window.destroy()
    elif action == "bounds":
        return {"x": window.x, "y": window.y, "width": window.width, "height": window.height, "maximized": maximized[0]}
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

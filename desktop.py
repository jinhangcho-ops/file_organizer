"""프로그램 창을 연다. exe의 실행 파일이자 유일한 실행 방법."""
import threading

import webview
from werkzeug.serving import make_server

import index
import server

http = make_server("127.0.0.1", 0, server.app, threaded=True)  # 0: 비어 있는 포트를 자동으로 고른다.
threading.Thread(target=http.serve_forever, daemon=True).start()
# 열쇠를 아는 이 창만 화면을 열 수 있다(브라우저로 주소만 열면 거부된다).
window = webview.create_window("파일 정리", f"http://127.0.0.1:{http.server_port}/?key={server.KEY}", width=1400, height=900)


def ask_folder():
    """폴더 고르는 대화상자. 고른 경로 또는 취소하면 None."""
    chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
    return chosen[0] if chosen else None


server.ask_folder = ask_folder
webview.start()  # 창을 닫으면 여기서 끝나고, 서버 스레드도 함께 끝난다.
index.finish()  # 색인 중이었으면 지금 파일까지 저장하고 멈춘다(다음에 남은 것부터 이어서 한다).

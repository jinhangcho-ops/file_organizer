"""오래 걸리는 작업(정리, 여러 파일 옮기기, 휴지통으로 보내기, 보관)을 뒤에서 하나씩 돌리고 진행률을 알린다. 화면은 /api/job을 읽는다."""
import threading
import time

status = {"running": False, "name": "", "done": 0, "total": 0, "current": "", "error": "", "result": None, "number": 0, "last": None}
lock = threading.Lock()


def progress(done, total=None, current=""):
    """작업이 부른다: 지금까지 한 수, 할 수, 지금 다루는 것."""
    status["done"] = done
    if total is not None:
        status["total"] = total
    status["current"] = current


def start(name, work):
    """work(progress)를 따로 돌린다. 다른 작업이 돌고 있으면 거부한다. 끝나면 work의 돌려준 값이 result에 남는다."""
    with lock:
        if status["running"]:
            raise ValueError(f"'{status['name']}' 작업이 아직 끝나지 않았습니다.")
        status.update(running=True, name=name, done=0, total=0, current="", error="", result=None, number=status["number"] + 1)

    def run():
        try:
            status["result"] = work(progress)
        except (OSError, ValueError) as e:
            status["error"] = str(e)
        except Exception as e:  # 화면에 알리고 끝낸다.
            status["error"] = f"작업 중 오류: {e}"
        finally:  # last: 끝난 작업의 결과(곧바로 다음 작업이 시작돼도 화면이 번호로 찾아 받는다)
            status.update(running=False, current="", last={"number": status["number"], "result": status["result"], "error": status["error"]})

    threading.Thread(target=run, daemon=True).start()
    return dict(status)


def wait(timeout=60):
    """돌고 있는 작업이 끝나기를(timeout초까지) 기다린다. 창을 닫은 뒤 desktop.py와 검사가 쓴다."""
    end = time.time() + timeout
    while status["running"] and time.time() < end:
        time.sleep(0.05)
    return dict(status)

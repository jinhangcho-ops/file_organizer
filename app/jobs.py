"""오래 걸리는 작업(정리, 여러 파일 옮기기, 휴지통으로 보내기, 보관)을 뒤에서 하나씩 돌리고 진행률을 알린다. 화면은 /api/job을 읽는다."""
import threading
import time

# 파일을 바꾸지 않고 읽기·받기만 하는 작업: 창을 닫으면 끊는다(desktop.py는 기다리지 않고, 화면의 [✕]도 묻지 않는다).
CUTTABLE = {"캐시 살펴보기", "게임 크기 세기", "모델 받기"}
status = {"running": False, "changes": False, "name": "", "done": 0, "total": 0, "current": "", "error": "", "result": None, "number": 0, "last": None}
lock = threading.Lock()
closing = False  # 창을 닫았거나 업데이트를 시작했다(desktop.py·update.install이 lock 안에서 켠다). 그 뒤의 start는 거부한다.


def progress(done, total=None, current=""):
    """작업이 부른다: 지금까지 한 수, 할 수, 지금 다루는 것."""
    status["done"] = done
    if total is not None:
        status["total"] = total
    status["current"] = current


def start(name, work):
    """work(progress)를 따로 돌린다. 다른 작업이 돌고 있으면 거부한다. 끝나면 work의 돌려준 값이 result에 남는다."""
    with lock:
        if closing:
            raise ValueError("프로그램을 닫는 중이라 작업을 시작하지 않았습니다.")
        if status["running"]:
            raise ValueError(f"'{status['name']}' 작업이 아직 끝나지 않았습니다.")
        status.update(running=True, changes=name not in CUTTABLE, name=name, done=0, total=0, current="", error="", result=None, number=status["number"] + 1)

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

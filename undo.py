"""Ctrl+Z 되돌리기의 기록: 앱 안에서 한 옮기기·이름 바꾸기·다운로드 정리의 (전, 후) 경로를 최근 KEEP개까지(프로그램을 켜 둔 동안만).
되돌리기는 actions.move_back이 하고, 휴지통으로 보낸 것은 기록하지 않는다(Windows 휴지통의 [복원]으로 꺼낸다)."""
import collections
import threading

KEEP = 20
history = collections.deque(maxlen=KEEP)  # [{"kind": "move"|"rename"|"organize", "pairs": [(전, 후)], "label"}]
guard = threading.Lock()


def record(kind, pairs, label):
    if pairs:
        with guard:
            history.append({"kind": kind, "pairs": list(pairs), "label": label})


def pop():
    """가장 최근 기록을 꺼낸다(없으면 None)."""
    with guard:
        return history.pop() if history else None


def put_back(entry):
    """되돌리기를 시작하지 못했을 때 기록을 제자리에 둔다."""
    with guard:
        history.append(entry)


def drop(kind):
    """그 종류의 기록을 모두 뺀다([마지막 정리 되돌리기] 버튼으로 정리를 되돌린 뒤)."""
    with guard:
        kept = [entry for entry in history if entry["kind"] != kind]
        history.clear()
        history.extend(kept)

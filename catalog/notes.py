"""폴더 메모(상세의 '내 메모'): 설정 폴더의 notes.json {비교용 key: {"path", "text"}}.
앱 안에서 옮기거나 이름을 바꾸면 따라가고(moved), 휴지통으로 보내면 지운다(removed). 앱 밖(탐색기)에서 옮긴 것은 따라가지 못한다."""
import os

from guard import exclude
from guard import fence
from app import settings

FILE = "notes.json"
MAX = 500  # 메모 한 개의 최대 글자 수


def load():
    data = settings.read(FILE, {})
    if not isinstance(data, dict):
        return {}

    def convert(data):  # 정션 경로의 메모와 실제 경로의 메모가 한 곳으로 모이면 둘을 이어 붙인다(MAX자까지)
        out = {}
        for v in data.values():
            path = exclude.real(v["path"], os.path.isdir(v["path"]))
            text = out.get(exclude.key(path), {}).get("text")
            text = v["text"] if text in (None, v["text"]) else (text + "\n" + v["text"])[:MAX]
            out[exclude.key(path)] = {**v, "path": path, "text": text}
        return out
    return exclude.once(FILE, data, convert)


def get(path):
    return (load().get(exclude.key(path)) or {}).get("text", "")


def put(path, text):
    """메모를 저장한다(빈 글이면 지운다). 저장한 글."""
    if not isinstance(text, str) or len(text) > MAX:
        raise ValueError(f"메모는 {MAX}자까지 쓸 수 있습니다.")
    data = load()
    text = text.strip()
    if text:
        data[exclude.key(path)] = {"path": os.path.abspath(path), "text": text}
    else:
        data.pop(exclude.key(path), None)
    settings.write(FILE, data)
    return text


def moved(pairs):
    """옮기거나 이름을 바꾼 (전, 후) 경로 안(또는 그 자신)의 메모 위치를 고친다."""
    data = load()
    changed = False
    for before, after in pairs:
        old = {exclude.key(before)}
        for key in [k for k, v in data.items() if fence.inside(v["path"], old)]:
            note = data.pop(key)
            path = after + note["path"][len(before):]
            data[exclude.key(path)] = {"path": path, "text": note["text"]}
            changed = True
    if changed:
        settings.write(FILE, data)


def removed(paths):
    """휴지통으로 보낸 경로 안(또는 그 자신)의 메모를 지운다."""
    gone = {exclude.key(p) for p in paths}
    data = load()
    kept = {k: v for k, v in data.items() if not fence.inside(v["path"], gone)}
    if len(kept) != len(data):
        settings.write(FILE, kept)

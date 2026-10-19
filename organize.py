"""정리: 정리할 곳 바로 아래의 파일을 놓을 곳의 종류별 폴더(문서, 사진 …)로 옮긴다. 하위 폴더는 건드리지 않는다.
- 받는 중인 파일과 숨김·시스템 파일은 옮기지 않는다.
- 같은 이름이 있으면 덮어쓰지 않고 '이름 (2).확장자'로.
- 같은 디스크면 이름 바꾸기, 다른 디스크면 복사 → 크기 확인 → 원본 지우기.
- 옮긴 기록(moves.json)으로 마지막 정리를 되돌린다."""
import os
import shutil

import drives
import exclude
import filetime
import index
import kinds
import settings

PARTIAL = {".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload"}  # 받는 중인 파일
LOG = "moves.json"


def free_name(folder, name, taken):
    """folder 안에서 쓰이지 않은 이름. taken은 이번에 쓰기로 한 경로(비교용 key)."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while True:
        path = os.path.join(folder, candidate)
        if not os.path.lexists(path) and exclude.key(path) not in taken:
            return path
        n += 1
        candidate = f"{stem} ({n}){ext}"


def plan(source, dest):
    """옮길 목록 [{"from", "to", "kind"}]. 옮기지 않고 보여 주기만 한다."""
    source, dest = settings.folder_text(source, "정리할 곳"), settings.folder_text(dest, "놓을 곳")
    moves, taken = [], set()
    with os.scandir(source) as entries:
        files = sorted((entry for entry in entries if entry.is_file() and not entry.is_symlink()), key=lambda e: e.name.lower())
    for entry in files:
        ext = os.path.splitext(entry.name)[1].lower()
        if ext in PARTIAL or index.hidden(entry.stat()):
            continue
        kind = kinds.of(ext)
        folder = os.path.join(dest, kind)
        if exclude.key(folder) == exclude.key(source):  # 이미 제자리에 있다.
            continue
        target = free_name(folder, entry.name, taken)
        taken.add(exclude.key(target))
        moves.append({"from": entry.path, "to": target, "kind": kind})
    return moves


def move(before, after):
    """파일 하나를 옮긴다. 다른 디스크면 복사해서 크기가 같은지 본 뒤에야 원본을 지운다."""
    os.makedirs(os.path.dirname(after), exist_ok=True)
    if drives.same_drive(before, after):
        os.rename(before, after)  # 대상이 있으면 실패한다(덮어쓰지 않는다).
        return
    size = os.stat(before).st_size
    try:
        filetime.copy(before, after)
        if os.stat(after).st_size != size:
            raise OSError("복사한 파일의 크기가 원본과 다릅니다.")
    except OSError:
        if os.path.exists(after):
            os.remove(after)
        raise
    os.remove(before)


def move_folder(before, after):
    """폴더를 통째로 옮긴다. 다른 디스크면 모든 파일을 복사해 크기를 확인한 뒤에야 원본 폴더를 지운다(복사가 실패하면 만든 사본만 치운다)."""
    if drives.same_drive(before, after):
        os.rename(before, after)
        return
    copied = []
    try:
        for folder, _, names in os.walk(before):
            target = os.path.join(after, os.path.relpath(folder, before))
            os.makedirs(target, exist_ok=True)
            for name in names:
                source = os.path.join(folder, name)
                filetime.copy(source, os.path.join(target, name))
                copied.append((source, os.path.join(target, name)))
        if any(os.stat(a).st_size != os.stat(b).st_size for a, b in copied):
            raise OSError("복사한 파일의 크기가 원본과 다릅니다.")
    except OSError:
        shutil.rmtree(after, ignore_errors=True)  # 이번에 만든 사본만 치운다. 원본은 그대로.
        raise
    shutil.rmtree(before)  # 옮기기: 사본을 확인했으므로 원본 자리를 비운다(탐색기의 '이동'과 같다).


def apply(moves, progress=lambda *args: None):
    """moves의 (전, 후)를 차례로 옮긴다. 옮긴 것과 실패한 것을 나눠 돌려주고 색인에 반영한다."""
    done, failed = [], []
    for n, (before, after) in enumerate(moves):
        progress(n, len(moves), before)
        try:
            if os.path.lexists(after):
                raise OSError("같은 이름의 파일이 이미 있습니다.")
            move(before, after)
            done.append((before, after))
        except OSError as e:
            failed.append({"from": before, "to": after, "error": e.strerror or str(e)})
    progress(len(moves), len(moves))
    if done:
        index.moved(done)
    return done, failed


def run(source, dest, progress=lambda *args: None):
    """미리보기와 같은 목록을 서버에서 다시 만들어 옮긴다(화면이 보낸 경로를 그대로 믿지 않는다)."""
    done, failed = apply([(item["from"], item["to"]) for item in plan(source, dest)], progress)
    settings.write(LOG, [{"from": before, "to": after} for before, after in done])
    return {"moved": len(done), "failed": failed}


def last():
    return settings.read(LOG, [])


def undo(progress=lambda *args: None):
    """마지막 정리를 되돌린다. 그사이 지웠거나 원래 자리에 같은 이름이 생긴 파일은 그대로 두고 알린다."""
    moves = last()
    if not moves:
        raise ValueError("되돌릴 정리가 없습니다.")
    back = [(item["to"], item["from"]) for item in moves if os.path.exists(item["to"])]
    missing = [{"from": item["to"], "to": item["from"], "error": "옮긴 파일이 없습니다."} for item in moves if not os.path.exists(item["to"])]
    done, failed = apply(back, progress)
    left = {after for _, after in back} - {after for _, after in done}  # 실패한 것만 기록에 남겨 다시 되돌릴 수 있게 한다.
    settings.write(LOG, [item for item in moves if item["from"] in left])
    return {"moved": len(done), "failed": missing + failed}

"""정리: 정리할 곳 바로 아래의 파일을 놓을 곳의 종류별 폴더(문서, 사진 …)로 옮긴다. 하위 폴더는 건드리지 않는다.
- 받는 중인 파일과 숨김·시스템 파일은 옮기지 않는다.
- 게임 캡처(Game Bar·NVIDIA 이름 모양)는 종류별 폴더 대신 captures의 게임별 폴더로.
- 프로그램 부품(PROGRAM_PARTS)은 옮기지 않고, 부품이 함께 있으면 exe도 옮기지 않는다(압축을 푼 게임·프로그램이 흩어지지 않게).
- 같은 이름이 있으면 덮어쓰지 않고 '이름 (2).확장자'로.
- 같은 디스크면 이름 바꾸기, 다른 디스크면 복사 → 크기 확인 → 원본 지우기.
- 옮긴 기록(moves.json)으로 마지막 정리를 되돌린다."""
import os
import shutil
import sqlite3
import stat

import captures
import drives
import exclude
import fence
import filetime
import index
import kinds
import settings
import undo as undo_log  # 이 파일에는 [마지막 정리 되돌리기]의 undo 함수가 있어 이름이 겹친다.

class Partial(OSError):
    """폴더를 다른 디스크로 옮겼지만 그동안 생기거나 바뀐 파일은 원래 폴더에 남겼다(사본은 옮긴 곳에 있다)."""


FILE_EXISTS = 80  # CopyFileW: 대상이 이미 있다
PARTIAL = {".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload"}  # 받는 중인 파일
LOG = "moves.json"
PROGRAM_PARTS = {".dll", ".sys", ".ocx", ".pak", ".ini", ".cfg", ".manifest", ".asi", ".vdf"}


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
    base, fences = captures.folders(), fence.keys()
    with_parts = any(os.path.splitext(entry.name)[1].lower() in PROGRAM_PARTS for entry in files)
    for entry in files:
        ext = os.path.splitext(entry.name)[1].lower()
        if ext in PARTIAL or ext in PROGRAM_PARTS or (with_parts and ext == ".exe") or index.hidden(entry.stat()) or not index.storable(entry.name) or os.path.isreserved(entry.name):
            continue
        kind = kinds.of(ext)
        folder = os.path.join(dest, kind)
        if exclude.key(folder) == exclude.key(source):  # 이미 제자리에 있다.
            continue
        target = captures.capture_target(entry.path, taken, base) or free_name(folder, entry.name, taken)
        if fence.inside(target, fences):
            continue
        taken.add(exclude.key(target))
        moves.append({"from": entry.path, "to": target, "kind": kind})
    return moves


def move(before, after):
    """파일 하나를 옮긴다. 다른 디스크면 복사해서 크기가 같은지 본 뒤에야 원본을 지운다."""
    os.makedirs(os.path.dirname(after), exist_ok=True)
    if drives.same_drive(before, after):
        os.rename(before, after)  # 대상이 있으면 실패한다(덮어쓰지 않는다).
        return
    st = os.stat(before)
    try:
        filetime.copy(before, after)
        if os.stat(after).st_size != st.st_size:
            raise OSError("복사한 파일의 크기가 원본과 다릅니다.")
    except OSError as e:
        if getattr(e, "winerror", None) != FILE_EXISTS and os.path.exists(after):  # 이름을 고른 뒤 남이 만든 파일이면 그대로 둔다.
            remove_copy(after)
        raise
    try:
        now = os.stat(before)
        if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):  # 복사하는 동안 다른 프로그램이 저장했다: 새 내용을 지우지 않는다
            raise OSError("옮기는 동안 원본이 바뀌어 옮기지 않았습니다.")
        remove_original(before)
    except OSError:  # 읽기 전용·사용 중: 원본은 그대로 두고 방금 만든 사본을 치운다(같은 파일이 두 곳에 남지 않게).
        remove_copy(after)
        raise


def remove_copy(path):
    """이번에 만든 사본 파일을 지운다. 복사는 읽기 전용 표시까지 가져오므로 그것을 풀고 지운다."""
    os.chmod(path, stat.S_IWRITE)
    os.remove(path)


def remove_original(path):
    """복사를 확인한 원본을 지운다. 읽기 전용이면 표시를 풀고 지운다(사본에는 표시가 남아 있다). 못 지우면(사용 중) 표시를 되돌린다."""
    mode = os.stat(path).st_mode
    os.chmod(path, stat.S_IWRITE)
    try:
        os.remove(path)
    except OSError:
        os.chmod(path, mode)
        raise


def reflect(pairs):
    """옮긴 (전, 후)를 색인에 반영한다. 실패하면(색인 중이라 DB가 잠김 등) 까닭 글을 돌려준다: 옮긴 것은 그대로이고 색인은 다음 색인 때 바로잡힌다.
    여기서 멈추면 되돌리기 기록·잠금 기록·메모가 빠져, 옮긴 파일을 되돌릴 수 없게 된다."""
    try:
        index.moved(pairs)
    except (OSError, sqlite3.Error) as e:
        return f"옮겼지만 색인에 반영하지 못했습니다(다음 색인 때 바로잡힙니다): {getattr(e, 'strerror', None) or e}"
    return None


def writable_then_retry(function, path, _):
    """rmtree가 이번에 만든 사본 중 읽기 전용 파일에서 멈추지 않게: 표시를 풀고 다시 지운다.
    그래도 안 되면(다른 프로그램이 여는 중) 그 사본은 남긴다: 실패 까닭은 원래 오류로 알린다."""
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        pass


def move_folder(before, after):
    """폴더를 통째로 옮긴다. 다른 디스크면 모든 파일을 복사해 크기를 확인한 뒤에야 복사한 원본 파일만 지운다(복사가 실패하면 만든 사본만 치운다).
    복사하는 동안 새로 생기거나 바뀐 파일은 지우지 않는다: 원래 폴더에 남기고 실패로 알린다(통째로 지우면 그 파일이 영구 삭제된다)."""
    if drives.same_drive(before, after):
        os.makedirs(os.path.dirname(after), exist_ok=True)  # 되돌릴 때 원래 위 폴더가 지워졌어도
        os.rename(before, after)
        return
    if os.path.islink(before) or os.path.isjunction(before):
        raise OSError("링크·정션은 다른 디스크로 옮기지 않습니다(가리키는 곳의 파일까지 옮겨진다).")
    copied = []

    def unreadable(error):  # 건너뛰면 그 폴더는 남는데 색인은 통째로 옮긴 것으로 고친다: 복사 전에 멈춘다
        raise OSError(f"안을 읽을 수 없는 폴더가 있어 옮기지 않았습니다: {error.filename}") from error

    os.makedirs(after)  # 이미 있으면 실패한다: 실패했을 때 치우는 사본(after)은 모두 이번에 만든 것이어야 한다.
    try:
        for folder, dirs, names in os.walk(before, onerror=unreadable):
            if any(os.path.islink(os.path.join(folder, d)) or os.path.isjunction(os.path.join(folder, d)) for d in dirs):
                raise OSError("링크·정션이 든 폴더는 다른 디스크로 옮기지 않습니다(가리키는 곳의 파일까지 옮겨진다).")
            target = os.path.join(after, os.path.relpath(folder, before))
            os.makedirs(target, exist_ok=True)
            for name in names:
                source = os.path.join(folder, name)
                st = os.stat(source)
                filetime.copy(source, os.path.join(target, name))
                copied.append((source, os.path.join(target, name), st))
        if any(os.stat(copy).st_size != st.st_size for _, copy, st in copied):
            raise OSError("복사한 파일의 크기가 원본과 다릅니다.")
    except OSError as e:
        shutil.rmtree(after, onexc=writable_then_retry)  # 이번에 만든 사본만 치운다. 원본은 그대로.
        if os.path.lexists(after):  # 다른 프로그램이 여는 중이라 다 치우지 못했다
            raise OSError(f"{e.strerror or e} (옮길 곳에 일부 사본이 남았습니다. 원본은 그대로입니다: {after})") from e
        raise
    kept = False
    for source, _, st in copied:
        try:
            now = os.stat(source)
        except OSError:  # 복사한 뒤 다른 프로그램이 지우거나 옮겼다: 여기서 멈추면 반쯤 옮긴 채 색인·되돌리기에 안 들어간다
            kept = True
            continue
        if (now.st_size, now.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
            try:
                remove_original(source)
            except OSError:  # 사용 중인 파일은 원래 폴더에 남긴다(Partial로 알린다).
                kept = True
        else:
            kept = True
    try:
        for folder, _, _ in os.walk(before, topdown=False):  # 빈 폴더만 지운다.
            os.rmdir(folder)
    except OSError:
        kept = True
    if kept:
        raise Partial("옮기는 동안 새로 생기거나 바뀐 파일과 다른 프로그램이 쓰는 중인 파일은 원래 폴더에 남겼습니다(나머지는 옮겼습니다).")


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
    error = done and reflect(done)
    if error:
        failed.append({"from": "색인", "to": "", "error": error})
    return done, failed


def run(source, dest, progress=lambda *args: None):
    """미리보기와 같은 목록을 서버에서 다시 만들어 옮긴다(화면이 보낸 경로를 그대로 믿지 않는다)."""
    done, failed = apply([(item["from"], item["to"]) for item in plan(source, dest)], progress)
    if done:  # 하나도 못 옮겼으면 앞 정리의 되돌리기 기록을 남긴다
        settings.write(LOG, [{"from": before, "to": after} for before, after in done])
    undo_log.record("organize", done, f"다운로드 정리 {len(done)}개")
    return {"moved": len(done), "failed": failed, "pairs": done}


def forget_undone(paths):
    """Ctrl+Z로 원래 자리(paths)에 되돌린 것을 [마지막 정리 되돌리기]의 기록에서 뺀다."""
    back = {exclude.key(p) for p in paths}
    settings.write(LOG, [item for item in last() if exclude.key(item["from"]) not in back])


def last():
    return settings.read(LOG, [])


def undo(progress=lambda *args: None):
    """마지막 정리를 되돌린다. 그사이 지웠거나 원래 자리에 같은 이름이 생긴 파일은 그대로 두고 알린다."""
    moves = last()
    if not moves:
        raise ValueError("되돌릴 정리가 없습니다.")
    fences = fence.keys()  # 그사이 차단하거나 잠근 폴더 안의 것은 건드리지 않는다.
    blocked, open_moves = [], []
    for item in moves:
        (blocked if fence.inside(item["to"], fences) or fence.inside(item["from"], fences) else open_moves).append(item)
    moves = open_moves
    back = [(item["to"], item["from"]) for item in moves if os.path.exists(item["to"])]
    missing = [{"from": item["to"], "to": item["from"], "error": "옮긴 파일이 없습니다."} for item in moves if not os.path.exists(item["to"])]
    missing += [{"from": item["to"], "to": item["from"], "error": "차단하거나 잠근 폴더라 되돌리지 않았습니다."} for item in blocked]
    done, failed = apply(back, progress)
    left = {after for _, after in back} - {after for _, after in done}  # 실패한 것만 기록에 남겨 다시 되돌릴 수 있게 한다.
    settings.write(LOG, [item for item in moves if item["from"] in left] + blocked)  # 막힌 것도 남겨 차단·잠금을 푼 뒤 되돌릴 수 있게 한다.
    undo_log.drop("organize")  # 버튼으로 되돌렸으면 Ctrl+Z의 정리 기록은 낡았다.
    return {"moved": len(done), "failed": missing + failed, "pairs": done}

"""파일·폴더 다루기: 옮기기(여러 개, 끌어 놓기·폴더 고르기), 이름 바꾸기, 휴지통으로 보내기, 되돌리기(Ctrl+Z).
덮어쓰지 않고, 끝나면 색인·잠금 기록·메모에 반영한다."""
import os

import drives
import exclude
import fence
import index
import lock
import notes
import organize
import trash
import undo

BAD = set('\\/:*?"<>|')  # 파일 이름에 쓸 수 없는 글자


def move_many(paths, folder, progress=lambda *args: None):
    """파일·폴더들을 folder로 옮긴다. 같은 이름이 있으면 덮어쓰지 않고 '이름 (2)'로. {"moved", "failed", "paths"(옮긴 뒤 경로)}"""
    if not os.path.isdir(folder):
        raise ValueError(f"없는 폴더입니다: {folder}")
    into, fences, records = exclude.key(folder), fence.keys(), [f["path"] for f in lock.load()["folders"]]

    def plan(path, _):
        if exclude.key(os.path.dirname(path)) == into:
            raise ValueError("이미 그 폴더에 있습니다.")
        if os.path.isdir(path) and fence.inside(into, {exclude.key(path)}):
            raise ValueError("폴더를 그 안으로 옮길 수 없습니다.")
        target = organize.free_name(folder, os.path.basename(path), set())
        fence.refuse(target, fences)  # 지운 차단 폴더 자리에 들어가지 않게
        lock.refuse_clash(path, target, records)
        return target

    done, failed = move_each([(path, None) for path in paths], plan, progress)
    if done:
        undo.record("move", done, f"{len(done)}개 옮기기")
    return {"moved": len(done), "failed": failed, "paths": [after for _, after in done]}


def move_each(pairs, plan, progress):
    """(옮길 것, 정해 둔 곳)마다 plan(옮길 것, 정해 둔 곳)이 검사하고 돌려준 곳으로 옮기고, 옮긴 것을 반영한다(moved). (옮긴 (전, 후)들, 실패들)"""
    done, failed = [], []
    for n, (src, dst) in enumerate(pairs):
        progress(n, len(pairs), src)
        try:
            target = plan(src, dst)
            (organize.move_folder if os.path.isdir(src) else organize.move)(src, target)
            done.append((src, target))
        except organize.Partial as e:  # 옮긴 곳의 것을 색인에 넣는다(원래 폴더에 남은 것은 다음 색인 때 들어간다).
            done.append((src, target))
            failed.append({"from": src, "error": str(e)})
        except (OSError, ValueError) as e:
            failed.append({"from": src, "error": getattr(e, "strerror", None) or str(e)})
    progress(len(pairs), len(pairs))
    if done:
        moved(done)
    return done, failed


def moved(pairs):
    """옮긴 (전, 후)들을 색인·잠금 기록(옮긴 폴더 안의 잠금 폴더·금고 위치)·메모에 반영한다."""
    index.moved(pairs)
    lock.moved(pairs)
    notes.moved(pairs)


def check_name(name):
    """새 이름(앞뒤 빈칸은 뗀다). 쓸 수 없는 이름이면 거부한다(이름 바꾸기와 새 폴더가 같이 쓴다)."""
    name = (name or "").strip()
    if not name or name in (".", "..") or BAD & set(name) or name.endswith("."):
        raise ValueError('쓸 수 없는 이름입니다. \\ / : * ? " < > | 는 쓸 수 없습니다.')
    return name


def rename(path, name):
    """같은 폴더 안에서 이름을 바꾼다. 같은 이름이 있으면 거부한다(대소문자만 바꾸는 것은 된다). 바뀐 경로를 돌려준다."""
    name = check_name(name)
    target = fence.refuse(os.path.join(os.path.dirname(path), name))
    if target == path:
        return path
    if os.path.lexists(target) and exclude.key(target) != exclude.key(path):
        raise ValueError(f"같은 이름의 파일이 있습니다: {name}")
    os.rename(path, target)
    moved([(path, target)])
    undo.record("rename", [(path, target)], f"이름 바꾸기 '{name}'")
    return target


def move_back(pairs, progress=lambda *args: None):
    """되돌리기: (전, 후)로 옮겼던 것을 거꾸로(나중 것부터) 원래 자리로. 옮기기와 같은 검사·반영을 하고, 덮어쓰지 않는다.
    원래 자리에 같은 이름이 생겼거나, 그사이 사라졌거나, 차단·잠금·구조 보호에 걸리면 그 항목은 그대로 두고 알린다.
    {"moved", "failed", "paths"(되돌린 원래 경로)}"""
    def plan(after, before):
        if not os.path.lexists(after):
            raise ValueError("옮긴 파일이 없습니다(그사이 옮기거나 지웠습니다).")
        if os.path.lexists(before) and exclude.key(before) != exclude.key(after):
            raise ValueError("원래 자리에 같은 이름이 있습니다.")
        fence.refuse(os.path.dirname(before))
        fence.refuse(after)  # 그사이 지금 자리를 차단·잠갔으면 꺼내지 않는다.
        fence.refuse_holding(after, drives.same_drive(after, before), moving=True)
        lock.refuse_clash(after, before)
        return before

    done, failed = move_each([(after, before) for before, after in reversed(pairs)], plan, progress)
    return {"moved": len(done), "failed": failed, "paths": [before for _, before in done]}


def trash_many(paths, progress=lambda *args: None):
    """파일·폴더들을 휴지통으로 보낸다(trash.to_trash). 보낼 수 없는 것은 그대로 두고 까닭을 알린다."""
    sent, failed = [], []
    for n, path in enumerate(paths):
        progress(n, len(paths), path)
        ok, refused = trash.to_trash([path])
        sent += ok
        failed += [{"from": p, "error": reason} for p, reason in refused]
    progress(len(paths), len(paths))
    if sent:
        index.removed(sent)
        notes.removed(sent)
    return {"trashed": len(sent), "failed": failed}


def folder_contents(folder):
    """폴더 비우기에 보낼 것: 바로 아래의 파일·폴더(숨김·시스템 파일은 빼고, 링크·정션은 링크만). ([경로], 뺀 숨김 수)"""
    paths, hidden = [], 0
    with os.scandir(folder) as entries:
        for entry in entries:
            try:
                if index.hidden(entry.stat(follow_symlinks=False)):
                    hidden += 1
                    continue
            except OSError:
                continue
            paths.append(entry.path)
    return sorted(paths, key=str.lower), hidden

"""파일·폴더 다루기: 옮기기(여러 개, 끌어 놓기·폴더 고르기), 이름 바꾸기, 휴지통으로 보내기. 덮어쓰지 않고, 끝나면 색인에 반영한다."""
import os

import exclude
import index
import lock
import organize
import trash

BAD = set('\\/:*?"<>|')  # 파일 이름에 쓸 수 없는 글자


def move_many(paths, folder, progress=lambda *args: None):
    """파일·폴더들을 folder로 옮긴다. 같은 이름이 있으면 덮어쓰지 않고 '이름 (2)'로. {"moved", "failed", "paths"(옮긴 뒤 경로)}"""
    if not os.path.isdir(folder):
        raise ValueError(f"없는 폴더입니다: {folder}")
    into = exclude.key(folder)
    done, failed = [], []
    for n, path in enumerate(paths):
        progress(n, len(paths), path)
        try:
            if exclude.key(os.path.dirname(path)) == into:
                raise ValueError("이미 그 폴더에 있습니다.")
            if os.path.isdir(path) and (into == exclude.key(path) or into.startswith(os.path.join(exclude.key(path), ""))):
                raise ValueError("폴더를 그 안으로 옮길 수 없습니다.")
            target = organize.free_name(folder, os.path.basename(path), set())
            (organize.move_folder if os.path.isdir(path) else organize.move)(path, target)
            done.append((path, target))
        except organize.Partial as e:  # 옮긴 곳의 것을 색인에 넣는다(원래 폴더에 남은 것은 다음 색인 때 들어간다).
            done.append((path, target))
            failed.append({"from": path, "error": str(e)})
        except (OSError, ValueError) as e:
            failed.append({"from": path, "error": getattr(e, "strerror", None) or str(e)})
    progress(len(paths), len(paths))
    if done:
        index.moved(done)
        lock.moved(done)  # 옮긴 폴더 안의 잠금 폴더·금고 위치를 비밀 파일에서 고친다.
    return {"moved": len(done), "failed": failed, "paths": [after for _, after in done]}


def rename(path, name):
    """같은 폴더 안에서 이름을 바꾼다. 같은 이름이 있으면 거부한다(대소문자만 바꾸는 것은 된다). 바뀐 경로를 돌려준다."""
    name = (name or "").strip()
    if not name or name in (".", "..") or BAD & set(name) or name.endswith("."):
        raise ValueError('쓸 수 없는 이름입니다. \\ / : * ? " < > | 는 쓸 수 없습니다.')
    target = os.path.join(os.path.dirname(path), name)
    if target == path:
        return path
    if os.path.lexists(target) and exclude.key(target) != exclude.key(path):
        raise ValueError(f"같은 이름의 파일이 있습니다: {name}")
    os.rename(path, target)
    index.moved([(path, target)])
    lock.moved([(path, target)])
    return target


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
    return {"trashed": len(sent), "failed": failed}

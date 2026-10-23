"""색인과 작업에서 빼는 폴더: 차단 폴더(blacklist)와 잠금 폴더·금고(lock). 그 폴더와 그 아래 전부를 다루지 않는다."""
import os

import blacklist
import exclude
import lock


def keys():
    """비교용 key들(exclude.key)."""
    return {exclude.key(path) for path in blacklist.load() + lock.folders()}


def inside(path, fences):
    """path가 fences(key들) 중 하나이거나 그 아래인지."""
    key = exclude.key(path)
    return any(key == fence or key.startswith(os.path.join(fence, "")) for fence in fences)


def holds(path, fences):
    """path가 fences 중 하나를 품는지(그 위 폴더인지)."""
    return any(inside(fence, {exclude.key(path)}) for fence in fences)


def overlaps(path, fences):
    """path가 fences 중 하나와 같거나, 그 안에 있거나, 그것을 품는지(새로 차단·잠금할 때 겹치지 않게)."""
    return inside(path, fences) or holds(path, fences)


def refuse(path, fences=None):
    """차단·잠금 폴더(또는 그 아래)면 거부한다. fences: 미리 만든 keys()(여러 개를 볼 때)."""
    if inside(path, keys() if fences is None else fences):
        raise ValueError(f"차단하거나 잠근 폴더라 다루지 않습니다: {path}")
    return path


def refuse_holding(path, locks_ok):
    """옮기기·이름 바꾸기·휴지통 전에: 차단 폴더를 품은 폴더는 늘 거부한다(차단 목록은 관리자만 고칠 수 있어 위치를 따라 고칠 수 없다).
    잠금 폴더·금고를 품은 폴더는 locks_ok(같은 디스크 안 옮기기·이름 바꾸기: lock.moved가 위치를 고친다)일 때만 받는다.
    다른 디스크로 옮기기는 복사가 잠긴 내용을 읽지 못하고, 휴지통은 비우면 잠긴 내용까지 사라지기 때문이다."""
    if holds(path, {exclude.key(p) for p in blacklist.load()}):
        raise ValueError(f"차단 폴더가 들어 있어 옮기거나 지울 수 없습니다: {path}")
    if not locks_ok and holds(path, {exclude.key(p) for p in lock.folders()}):
        raise ValueError(f"잠금 폴더(또는 금고)가 들어 있어 휴지통으로 보내거나 다른 디스크로 옮길 수 없습니다: {path}")

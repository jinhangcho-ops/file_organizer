"""색인과 작업에서 빼는 폴더: 차단 폴더(blacklist)와 잠금 폴더(lock, 열어 둔 것도). 그 폴더와 그 아래 전부를 다루지 않는다."""
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


def overlaps(path, fences):
    """path가 fences 중 하나와 같거나, 그 안에 있거나, 그것을 품는지(새로 차단·잠금할 때 겹치지 않게)."""
    return inside(path, fences) or any(inside(fence, {exclude.key(path)}) for fence in fences)


def refuse(path):
    """차단·잠금 폴더(또는 그 아래)면 거부한다."""
    if inside(path, keys()):
        raise ValueError(f"차단하거나 잠근 폴더라 다루지 않습니다: {path}")
    return path

"""앱 안의 폴더·파일 고르기 창(화면 공유에서 창 하나만 공유해도 보이게 Windows 고르기 창 대신 쓴다)이 읽는 목록과 새 폴더 만들기.
숨김·시스템 폴더, 링크·정션, 차단·잠금 폴더(와 그 아래)는 보여 주지도 들어가지도 않는다."""
import os

from work import actions
from app import drives
from guard import fence
from catalog import index


def places():
    """맨 처음 목록: 디스크들과 알려진 폴더(다운로드·문서·바탕 화면)."""
    known = [{"name": name, "path": path} for name, path in drives.known_folders().items() if path and os.path.isdir(path)]
    disks = [{"name": f"{d['path']} ({d['type']})", "path": d["path"]} for d in drives.list_drives()]
    return {"path": "", "parent": None, "folders": known + disks, "files": []}


def listing(path, ext=None):
    """path 바로 아래의 폴더(와 ext로 끝나는 파일)들, 이름 순. path가 비면 places(). {"path", "parent"("": 맨 처음), "folders", "files"}"""
    if not path:
        return places()
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError(f"없는 폴더입니다: {path}")
    fences = fence.keys()
    fence.refuse(path, fences)
    fence.refuse(os.path.realpath(path), fences)  # 경로 칸에 친 정션이 차단·잠금 폴더를 가리킬 수 있다
    folders, files = [], []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink() or entry.is_junction():
                        continue
                    st = entry.stat(follow_symlinks=False)
                    if index.hidden(st):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not fence.inside(entry.path, fences):
                            folders.append({"name": entry.name, "path": entry.path})
                    elif ext and entry.name.lower().endswith(ext):
                        files.append({"name": entry.name, "path": entry.path, "size": st.st_size})
                except OSError:
                    continue
    except PermissionError:
        raise ValueError(f"열어 볼 수 없는 폴더입니다: {path}") from None
    by_name = lambda item: item["name"].lower()  # noqa: E731
    parent = os.path.dirname(path)
    return {"path": path, "parent": "" if parent == path else parent, "folders": sorted(folders, key=by_name), "files": sorted(files, key=by_name)}


def make_folder(path, name):
    """path 안에 새 폴더 name을 만든다. 같은 이름이 있으면 거부한다. 만든 폴더의 경로."""
    name = actions.check_name(name)
    if not path or not os.path.isdir(path):
        raise ValueError("새 폴더를 만들 곳을 먼저 여세요.")
    fence.refuse_system(fence.refuse(os.path.abspath(path)))
    fence.refuse_system(fence.refuse(os.path.realpath(path)))  # 정션이 가리키는 곳도
    target = os.path.join(os.path.abspath(path), name)
    if os.path.lexists(target):
        raise ValueError(f"같은 이름이 있습니다: {name}")
    os.mkdir(target)
    return target

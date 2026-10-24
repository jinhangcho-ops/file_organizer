"""캐시 정리: Windows 임시 폴더, 브라우저 캐시, 자주 쌓이는 앱 캐시에서 오래된 것을 휴지통으로 보낸다(영구 삭제는 하지 않는다).
- 캐시 폴더 바로 아래의 파일·폴더 하나씩, 그 안에서 가장 늦게 고친 때가 기준(일)보다 오래된 것만.
- 숨김·시스템 파일, 링크·정션, 차단·잠금·구조 보호 폴더 안은 건드리지 않는다.
- 그 프로그램이 켜져 있으면 그 캐시는 건너뛴다(쓰는 중인 캐시를 빼면 프로그램이 꼬일 수 있다).
화면은 고른 캐시의 이름(PLACES의 id)만 보내고, 지울 것은 서버가 다시 찾는다."""
import glob
import os
import subprocess
import time
from pathlib import Path

import actions
import exclude
import fence
import protect

DAY = 86400
GROUPS = {"temp": "Windows 임시 폴더", "browser": "브라우저 캐시", "app": "앱 캐시"}
PLACES = [  # (id, 이름, 묶음, 켜져 있으면 건너뛸 프로그램(소문자), 캐시 폴더(환경 변수·* 가능))
    ("temp", "임시 파일(%TEMP%)", "temp", (), [r"%TEMP%"]),
    ("edge", "Edge", "browser", ("msedge.exe",), [r"%LOCALAPPDATA%\Microsoft\Edge\User Data\*\Cache\Cache_Data"]),
    ("chrome", "Chrome", "browser", ("chrome.exe",), [r"%LOCALAPPDATA%\Google\Chrome\User Data\*\Cache\Cache_Data"]),
    ("firefox", "Firefox", "browser", ("firefox.exe",), [r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*\cache2\entries"]),
    ("discord", "Discord", "app", ("discord.exe",), [r"%APPDATA%\discord\Cache\Cache_Data"]),
    ("teams", "Teams", "app", ("teams.exe", "ms-teams.exe"), [r"%APPDATA%\Microsoft\Teams\Cache",
                                                             r"%LOCALAPPDATA%\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\EBWebView\*\Cache\Cache_Data"]),
    ("slack", "Slack", "app", ("slack.exe",), [r"%APPDATA%\Slack\Cache\Cache_Data"]),
    ("vscode", "VS Code", "app", ("code.exe",), [r"%APPDATA%\Code\Cache\Cache_Data"]),
    ("spotify", "Spotify", "app", ("spotify.exe",), [r"%LOCALAPPDATA%\Spotify\Data"]),
]
IDS = [place[0] for place in PLACES]
SKIP = 0x2 | 0x4 | 0x400  # 숨김, 시스템, 링크·정션(reparse point)


def folders(patterns):
    """캐시 폴더들(있는 진짜 폴더만). 환경 변수가 없는 패턴은 건너뛴다.
    드라이브 맨 위이거나 사용자 폴더를 품은 곳은 뺀다(TEMP가 C:\\ 나 C:\\Users\\나 를 가리키면 그 아래 전부가 '캐시'가 된다)."""
    home = {exclude.key(str(Path.home()))}
    found = []
    for pattern in patterns:
        expanded = os.path.expandvars(pattern)
        if "%" not in expanded:
            found += [p for p in glob.glob(expanded) if os.path.isdir(p) and not os.path.islink(p)
                      and os.path.dirname(os.path.abspath(p)) != os.path.abspath(p) and not fence.holds(p, home)]
    return found


def running():
    """켜져 있는 프로그램 이름들(소문자). tasklist를 창 없이 부른다."""
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return {line.split('","')[0].strip('"').lower() for line in result.stdout.splitlines() if line}


def newest_and_size(path):
    """(그 안에서 가장 늦게 고친 때, 크기). 폴더는 링크를 따라가지 않고 안을 모두 본다."""
    st = os.stat(path, follow_symlinks=False)
    if not os.path.isdir(path):
        return st.st_mtime, st.st_size
    newest, total = st.st_mtime, 0
    for folder, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(folder, d))]
        for name in names:
            try:
                item = os.stat(os.path.join(folder, name), follow_symlinks=False)
            except OSError:
                continue
            newest, total = max(newest, item.st_mtime), total + item.st_size
    return newest, total


def old_items(patterns, days):
    """[(경로, 크기)] 캐시 폴더 바로 아래에서 days일 넘게 고치지 않은 것."""
    cutoff, kept = time.time() - days * DAY, fence.keys() | protect.keys()
    found = []
    for folder in folders(patterns):
        if fence.inside(folder, kept):
            continue
        try:
            with os.scandir(folder) as entries:
                children = [e for e in entries if not e.stat(follow_symlinks=False).st_file_attributes & SKIP]
        except OSError:
            continue
        for entry in children:
            try:
                newest, size = newest_and_size(entry.path)
            except OSError:
                continue
            if newest < cutoff and not fence.holds(entry.path, kept):
                found.append((entry.path, size))
    return found


def scan(days, progress=lambda *args: None):
    """{"places": 캐시마다 {"id", "name", "group", "found"(캐시 폴더가 있음), "running", "count", "bytes"}, "groups": GROUPS}."""
    on = running()
    report = []
    for n, (place, name, group, programs, patterns) in enumerate(PLACES):
        progress(n, len(PLACES), name)
        items = old_items(patterns, days)
        report.append({"id": place, "name": name, "group": group, "found": bool(folders(patterns)), "running": bool(on & set(programs)),
                       "count": len(items), "bytes": sum(size for _, size in items)})
    progress(len(PLACES), len(PLACES))
    return {"places": report, "groups": GROUPS}


def clean(ids, days, progress=lambda *args: None):
    """고른 캐시(ids)의 오래된 것을 휴지통으로. 켜져 있는 프로그램의 캐시는 건너뛰고 알린다."""
    on = running()
    paths, skipped = [], []
    for place, name, _, programs, patterns in PLACES:
        if place not in ids:
            continue
        if on & set(programs):
            skipped.append({"from": name, "error": "프로그램이 켜져 있어 건너뛰었습니다. 닫은 뒤 다시 하세요."})
            continue
        paths += [path for path, _ in old_items(patterns, days)]
    result = actions.trash_many(list(dict.fromkeys(paths)), progress)
    return result | {"failed": skipped + result["failed"]}


def check_ids(ids):
    if not isinstance(ids, list) or any(i not in IDS for i in ids):
        raise ValueError("캐시 목록이 잘못됐습니다.")
    return list(dict.fromkeys(ids))

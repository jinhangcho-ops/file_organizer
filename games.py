"""게임 폴더 찾기. 색인은 폴더를 훑으며 게임 폴더인지만 판별하고 그 안은 읽지 않는다(index.walk). 찾은 폴더는 색인의 games 표에 남는다.
런처의 기록(Steam·Epic·GOG·Ubisoft)은 이름·크기·마지막 실행을 더해 준다. 게임 폴더와 그 안은 옮기기·이름 바꾸기·휴지통·정리에서 다루지 않는다."""
import glob
import json
import os
import re
import time
import winreg

import blacklist
import exclude
import fence
import index

# 게임 폴더에만 있는 파일(소문자): Steam·Epic·GOG·Ubisoft의 연동 라이브러리, Unity 엔진.
MARKERS = {"steam_api.dll", "steam_api64.dll", "steam_appid.txt", "unityplayer.dll", "gameassembly.dll", "eossdk-win64-shipping.dll",
           "galaxy.dll", "galaxy64.dll", "uplay_r1_loader64.dll", "upc_r2_loader64.dll"}
STEAM_FOLDERS = {"steam", "steamapps", "steamlibrary"}  # 그 안의 게임은 Steam 기록(appmanifest)으로 안다.
FRESH = 60  # 런처 기록을 다시 읽기까지의 초(검사할 때마다 레지스트리와 폴더를 읽지 않게)
KEYS_FRESH = 5  # 게임 폴더 key를 다시 읽기까지의 초. 휴지통·폴더 비우기가 항목마다 묻는데, 한 번 읽는 데 약 9ms라 5,000개면 1분이 넘었다.
memo = {"at": 0.0, "games": []}
keys_memo = {"at": 0.0, "keys": set()}


def is_game(file_names, folder_names):
    """폴더 바로 아래의 파일·폴더 이름(소문자)으로 판별: 표시 파일이 있거나, Unreal 게임처럼 exe와 Engine 폴더가 함께 있다."""
    return bool(MARKERS & file_names) or ("engine" in folder_names and any(n.endswith(".exe") for n in file_names))


def container_games(path):
    """게임 폴더 이름(blacklist.GAMES)인 폴더 안의 게임들. Steam 폴더는 Steam 기록으로 알아서 넣지 않는다."""
    if os.path.basename(path).lower() in STEAM_FOLDERS:
        return []
    return blacklist.subfolders(path)


def value(root, path, name):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def subkeys(root, path):
    try:
        with winreg.OpenKey(root, path) as key:
            return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
    except OSError:
        return []


def vdf(text, name):
    """Steam 설정 글(.vdf·.acf)에서 "이름" "값"의 값들."""
    return [v.replace("\\\\", "\\") for v in re.findall(r'"%s"\s+"([^"]*)"' % re.escape(name), text, re.I)]


def read(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def number(text):
    return int(text) if text and text.isdigit() else None


def steam(base=None):
    """Steam에 설치한 게임: 라이브러리 폴더(libraryfolders.vdf)마다 appmanifest_번호.acf."""
    base = base or value(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
    if not base:
        return []
    libraries = [os.path.normpath(base)] + [os.path.normpath(p) for p in vdf(read(os.path.join(base, "steamapps", "libraryfolders.vdf")), "path")]
    found = []
    for library in dict.fromkeys(libraries):
        for manifest in glob.glob(os.path.join(glob.escape(library), "steamapps", "appmanifest_*.acf")):
            text = read(manifest)
            name, folder = (vdf(text, "name") or [""])[0], (vdf(text, "installdir") or [""])[0]
            if folder:
                found.append({"name": name or folder, "launcher": "Steam", "path": os.path.join(library, "steamapps", "common", folder),
                              "size": number((vdf(text, "SizeOnDisk") or [""])[0]), "played": number((vdf(text, "LastPlayed") or [""])[0]) or None,
                              "steam_id": (vdf(text, "appid") or [""])[0]})
    return found


def epic(folder=None):
    """Epic Games에 설치한 게임: 매니페스트(.item, JSON)."""
    folder = folder or os.path.join(os.environ.get("PROGRAMDATA") or r"C:\ProgramData", "Epic", "EpicGamesLauncher", "Data", "Manifests")
    found = []
    for item in glob.glob(os.path.join(glob.escape(folder), "*.item")):
        try:
            data = json.loads(read(item))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("InstallLocation"):
            found.append({"name": data.get("DisplayName") or os.path.basename(data["InstallLocation"]), "launcher": "Epic Games",
                          "path": os.path.normpath(data["InstallLocation"]), "size": data.get("InstallSize") if isinstance(data.get("InstallSize"), int) else None,
                          "played": None})
    return found


def registry_games():
    """GOG·Ubisoft Connect가 레지스트리에 남긴 설치 폴더."""
    found = []
    base = r"SOFTWARE\WOW6432Node\GOG.com\Games"
    for sub in subkeys(winreg.HKEY_LOCAL_MACHINE, base):
        path = value(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub, "path")
        if path:
            found.append({"name": value(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub, "gameName") or os.path.basename(path), "launcher": "GOG",
                          "path": os.path.normpath(path), "size": None, "played": None})
    base = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
    for sub in subkeys(winreg.HKEY_LOCAL_MACHINE, base):
        path = value(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub, "InstallDir")
        if path:
            path = os.path.normpath(path)
            found.append({"name": os.path.basename(path), "launcher": "Ubisoft Connect", "path": path, "size": None, "played": None})
    return found


def launcher_games():
    """런처가 아는 게임들(있는 폴더만). FRESH초 동안은 읽어 둔 것을 쓴다."""
    if time.time() - memo["at"] > FRESH:
        found = []
        for source in (steam, epic, registry_games):
            try:
                found += source()
            except OSError:
                continue
        memo.update(at=time.time(), games=[g for g in found if os.path.isdir(g["path"])])
    return memo["games"]


def found_games():
    """색인이 찾아 둔 게임 폴더들(games 표, 그 뒤에 지운 폴더는 뺀다)."""
    db = index.connect()
    try:
        return [dict(row) for row in db.execute("select path, size from games order by lower(path)") if os.path.isdir(row["path"])]
    finally:
        db.close()


def keys():
    """게임 폴더들의 비교용 key(exclude.key). KEYS_FRESH초 동안은 읽어 둔 것을 쓴다(색인이 games 표를 고치면 forget)."""
    if time.time() - keys_memo["at"] > KEYS_FRESH:
        keys_memo.update(at=time.time(), keys=fence.both([g["path"] for g in launcher_games()]) | {exclude.key(g["path"]) for g in found_games()})
    return keys_memo["keys"]


def forget():
    """games 표가 바뀌었다: 다음 keys()가 다시 읽게 한다."""
    keys_memo["at"] = 0.0


def listing():
    """화면의 '게임': 색인이 찾은 것과 런처가 아는 것을 합친다(같은 폴더는 런처의 이름·크기·마지막 실행을 쓴다). 이름 순."""
    merged = {exclude.key(g["path"]): {"name": os.path.basename(g["path"]), "launcher": "", "path": g["path"], "size": g["size"], "played": None}
              for g in found_games()}
    for g in launcher_games():
        known = merged.get(exclude.key(g["path"]), {})
        merged[exclude.key(g["path"])] = {**g, "size": g["size"] or known.get("size")}
    return sorted(({k: g[k] for k in ("name", "launcher", "path", "size", "played")} for g in merged.values()), key=lambda g: g["name"].lower())


def folder_size(path):
    """게임 폴더의 크기(링크·정션은 따라가지 않는다). 읽을 수 없는 곳은 건너뛴다."""
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir():
                            stack.append(entry.path)
                        else:
                            total += entry.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def measure(progress):
    """크기를 모르는 게임 폴더의 크기를 세어 games 표에 남긴다(뒤의 작업)."""
    todo = [g for g in listing() if g["size"] is None]
    db = index.connect()
    try:
        for n, g in enumerate(todo):
            progress(n, len(todo), g["path"])
            size = folder_size(g["path"])
            db.execute("insert into games(key, path, size) values(?, ?, ?) on conflict(key) do update set size = excluded.size",
                       (exclude.key(g["path"]), g["path"], size))
            db.commit()
        index.totals(db)  # 왼쪽 폴더 목록의 용량에 센 크기를 더한다.
        db.commit()
    finally:
        db.close()
    progress(len(todo), len(todo))
    return {"measured": len(todo)}


def steam_names():
    """Steam 게임 번호 → 이름(설치한 것만). 스크린샷 폴더 이름(번호)을 게임 이름으로 바꿀 때 쓴다."""
    return {g["steam_id"]: g["name"] for g in launcher_games() if g.get("steam_id")}

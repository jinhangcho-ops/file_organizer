"""잠금 폴더: 비밀번호를 맞춰야 열리는 폴더. Windows 권한으로 가린다(암호화가 아니다). 두 가지 방법이 있다.
- 제자리에서 잠그기: 그 폴더에 내 계정의 '거부' 권한(목록·읽기·쓰기·만들기·지나가기·지우기, 그 아래 전부)을 붙이고 숨김·시스템 속성을 켠다.
- 금고로 모으기: 폴더를 금고(사용자가 고른 곳의 .vault, 같은 디스크) 안으로 무작위 이름으로 옮기고, 금고 하나만 위처럼 잠근다.
권한 보기·바꾸기는 거부하지 않는다. 그래서 관리자 권한 없이 다시 풀 수 있다(모든 권한(F)을 거부하면 관리자만 풀 수 있다).
목록(원래 위치, 금고 안 이름, 열림)과 비밀번호의 PBKDF2 해시는 비밀 파일 lock.dat 하나에 DPAPI(이 Windows 계정만 푸는 암호화)로 둔다.
앱 안에서 위 폴더를 옮기거나 이름을 바꾸면 moved가 위치를 고친다. 열어 둔 것은 창을 닫을 때와 다음에 켤 때 다시 잠근다."""
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
from ctypes import wintypes

import drives
import exclude
import fence
import index
import organize
import settings

FILE = "lock.dat"
OLD_FILE = "lock.json"  # 예전에 암호화하지 않고 두던 곳. 읽으면 lock.dat으로 옮기고 지운다.
VAULT = ".vault"
ROUNDS = 200_000
MIN_LENGTH = 4
DENY = "(OI)(CI)(RD,WD,AD,X,DC,D)"
HIDE = 0x2 | 0x4  # 숨김 | 시스템
sid = [None]  # 지금 사용자의 SID(한 번 묻고 기억한다)
guard = threading.RLock()  # lock.dat 읽고-고치고-쓰기를 한 번에 하나만(옮기기 작업의 moved와 화면 요청이 동시에 쓰면 한쪽이 사라진다)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetFileAttributesW.restype = wintypes.DWORD
kernel.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
kernel.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel.LocalFree.argtypes = [ctypes.c_void_p]
crypt = ctypes.WinDLL("crypt32", use_last_error=True)


def one_at_a_time(work):
    def run(*args, **kwargs):
        with guard:
            return work(*args, **kwargs)
    return run


class NoPlace(ValueError):
    """꺼낼 원래 위 폴더가 없다(화면이 폴더를 골라 다시 부른다)."""


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def dpapi(data, protect):
    """DPAPI로 암호화(protect)하거나 푼다. 이 Windows 계정으로 로그인했을 때만 풀린다."""
    buffer = ctypes.create_string_buffer(data, len(data))
    source, out = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    call = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out)):  # 1: 창을 띄우지 않는다
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        kernel.LocalFree(out.data)


def store_path():
    return settings.folder() / FILE


def load():
    """비밀 파일을 읽는다. {"hash", "salt", "vault", "vault_open", "folders": [{"path", "open"} 또는 {"path", "id"}(금고)]}"""
    path = store_path()
    if path.exists():
        data = json.loads(dpapi(path.read_bytes(), False).decode("utf-8"))
    else:
        data = settings.read(OLD_FILE, {})
        if data:
            save(data)
            (settings.folder() / OLD_FILE).unlink()  # 이 앱의 설정 파일(사용자 파일 아님). 위에서 lock.dat으로 옮겼다.
    return {"hash": data.get("hash"), "salt": data.get("salt"), "vault": data.get("vault") or "", "vault_open": bool(data.get("vault_open")),
            "folders": [f for f in data.get("folders", []) if isinstance(f, dict) and isinstance(f.get("path"), str)]}


def save(data):
    path = store_path()
    temp = path.with_suffix(".tmp")
    temp.write_bytes(dpapi(json.dumps(data, ensure_ascii=False).encode("utf-8"), True))
    os.replace(temp, path)
    kernel.SetFileAttributesW(str(path), 0x2)  # 숨김. 바꿔치기하면 속성이 빠져 다시 켠다.


def folders():
    """색인·작업에서 빼는 곳: 제자리 잠금 폴더들과 금고."""
    data = load()
    return [f["path"] for f in data["folders"] if not f.get("id")] + ([data["vault"]] if data["vault"] else [])


def listing():
    """화면에 보여 줄 잠금 폴더들 [{"path", "open", "vault", "missing", "sealed"}]. 금고 안의 것은 금고가 열려 있으면 open.
    sealed: 잠겨 있어야 할 것이 실제로 잠겼는지(sealed()로 확인). 열려 있거나 없는 것은 None."""
    data = load()
    vault = sealed(data["vault"]) if data["vault"] and not data["vault_open"] else None
    rows = []
    for f in data["folders"]:
        is_open = data["vault_open"] if f.get("id") else bool(f.get("open"))
        missing = not f.get("id") and not os.path.isdir(f["path"])
        check = None if is_open or missing else vault if f.get("id") else sealed(f["path"])
        rows.append({"path": f["path"], "vault": bool(f.get("id")), "open": is_open, "missing": missing, "sealed": check})
    return rows


def sealed(path):
    """실제로 잠겼는지: 숨김·시스템 속성이 켜져 있고, 내 계정으로 폴더 안을 열면 Windows가 막는다.
    앱 밖에서 권한을 고쳤거나(icacls, 탐색기의 보안 탭) 잠그다 중간에 실패했으면 False다."""
    now = kernel.GetFileAttributesW(path)
    if now == 0xFFFFFFFF or now & HIDE != HIDE:
        return False
    try:
        with os.scandir(path):
            return False  # 열렸다: 거부 권한이 없다
    except PermissionError:
        return True
    except OSError:
        return False


def location(data, item):
    """잠금 기록의 실제 폴더: 제자리 것은 그 폴더, 금고에 든 것은 금고 안 무작위 이름 폴더. 열려 있지 않으면 None."""
    if item.get("id"):
        return os.path.join(data["vault"], item["id"]) if data["vault_open"] else None
    return item["path"] if item.get("open") else None


def opened_place(path):
    """path가 열어 둔 잠금 폴더(또는 그 아래)이면 (그 폴더의 실제 위치, 원래 위치). 아니면 ValueError.
    잠금 파일 보기는 열어 둔 것만 보이고 연다(잠긴 것은 비밀번호로 연 뒤에)."""
    data = load()
    if isinstance(path, str) and path:
        key = exclude.key(os.path.abspath(path))
        for item in data["folders"]:
            place = location(data, item)
            if place and fence.inside(key, {exclude.key(place)}):
                return place, item["path"]
    raise ValueError("열어 둔 잠금 폴더 안이 아닙니다. 잠금 관리에서 먼저 여세요.")


def files(path=""):
    """잠금 파일 보기. path가 비면 잠금 폴더들 {"folders": [{"path", "label", "open"}]}, 아니면 열어 둔 잠금 폴더 안의
    {"path", "label"(원래 위치로 쓴 경로), "parent"(""면 처음으로), "folders": [{"name", "path"}], "files": [{"name", "path", "size", "modified"}]}."""
    if not path:
        data = load()
        return {"path": "", "label": "", "parent": "", "files": [],
                "folders": [{"path": location(data, f) or "", "label": f["path"], "open": bool(location(data, f))} for f in data["folders"]]}
    place, original = opened_place(path)
    path = os.path.abspath(path)
    folders, found = [], []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():  # 잠금 폴더 밖을 가리킬 수 있다
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders.append({"name": entry.name, "path": entry.path})
                else:
                    st = entry.stat(follow_symlinks=False)
                    found.append({"name": entry.name, "path": entry.path, "size": st.st_size, "modified": st.st_mtime})
            except OSError:
                continue
    by_name = lambda item: item["name"].lower()  # noqa: E731
    same = exclude.key(path) == exclude.key(place)
    return {"path": path, "label": original + path[len(place):], "parent": "" if same else os.path.dirname(path),
            "folders": sorted(folders, key=by_name), "files": sorted(found, key=by_name)}


def open_file(path):
    """열어 둔 잠금 폴더 안의 파일을 연결된 프로그램으로 연다."""
    opened_place(path)
    if not os.path.isfile(path):
        raise ValueError(f"없는 파일입니다: {path}")
    os.startfile(path)


def vault_info():
    data = load()
    return {"path": data["vault"], "open": data["vault_open"], "count": sum(1 for f in data["folders"] if f.get("id"))}


def has_password():
    return bool(load()["hash"])


def digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ROUNDS).hex()


def verify(password):
    data = load()
    if not data["hash"]:
        raise ValueError("먼저 잠금 비밀번호를 정하세요.")
    if not isinstance(password, str) or not hmac.compare_digest(digest(password, data["salt"]), data["hash"]):
        raise ValueError("비밀번호가 맞지 않습니다.")


@one_at_a_time
def set_password(new, old=None):
    """비밀번호를 정하거나 바꾼다. 이미 있으면 old가 맞아야 한다."""
    data = load()
    if data["hash"]:
        verify(old)
    if not isinstance(new, str) or len(new) < MIN_LENGTH:
        raise ValueError(f"비밀번호는 {MIN_LENGTH}자 이상으로 정하세요.")
    salt = secrets.token_hex(16)
    save({**data, "hash": digest(new, salt), "salt": salt})


def user_sid():
    if sid[0] is None:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, encoding="mbcs", creationflags=0x08000000).stdout
        sid[0] = out.strip().split(",")[-1].strip('"')
        if not sid[0].startswith("S-1-"):
            raise OSError("지금 사용자의 SID를 알 수 없습니다.")
    return sid[0]


def icacls(*args):
    result = subprocess.run(["icacls", *args], capture_output=True, text=True, encoding="mbcs", creationflags=0x08000000)
    if result.returncode:
        raise OSError(f"폴더 권한을 바꾸지 못했습니다: {(result.stdout or result.stderr).strip()}")


def attributes(path, hide):
    now = kernel.GetFileAttributesW(path)
    if now == 0xFFFFFFFF:
        raise OSError(f"폴더 속성을 읽지 못했습니다: {path}")
    if not kernel.SetFileAttributesW(path, (now | HIDE) if hide else (now & ~HIDE)):
        raise OSError(f"폴더 속성을 바꾸지 못했습니다: {path}")


def seal(path):
    """속성을 먼저 바꾼다(거부 권한을 붙인 뒤에는 속성을 바꿀 수 없다). 권한을 못 붙이면 속성을 되돌린다."""
    attributes(path, True)
    try:
        icacls(path, "/deny", f"*{user_sid()}:{DENY}")
    except OSError:
        attributes(path, False)
        raise


def unseal(path):
    icacls(path, "/remove:d", f"*{user_sid()}")
    attributes(path, False)


def record(data, path):
    for f in data["folders"]:
        if exclude.key(f["path"]) == exclude.key(path):
            return f
    raise ValueError(f"잠금 폴더가 아닙니다: {path}")


def put(data, item):
    """item(같은 원래 위치의 기록은 바꾼다)을 넣어 저장한다. item이 {"path"}만 있으면 그 기록을 뺀다."""
    kept = [f for f in data["folders"] if exclude.key(f["path"]) != exclude.key(item["path"])]
    save({**data, "folders": kept + ([item] if len(item) > 1 else [])})


def check_new(path, others):
    """새로 잠그거나 금고로 모을 수 있는 폴더인지. others: 겹치면 안 되는 차단 폴더 key들."""
    path = index.real(path)  # 기록은 색인처럼 실제 경로로(정션 경로로 적으면 옮기기·휴지통 검사가 어긋난다)
    if not os.path.isdir(path) or os.path.dirname(path) == path:
        raise ValueError(f"잠글 수 있는 폴더가 아닙니다: {path}")
    if fence.overlaps(path, fence.both(folders()) | set(others)):
        raise ValueError(f"이미 잠갔거나 차단한 폴더(또는 금고)와 겹칩니다: {path}")
    if any(exclude.key(f["path"]) == exclude.key(path) for f in load()["folders"]):  # 기록은 원래 위치로 찾으므로 겹치면 금고 기록이 덮인다.
        raise ValueError(f"금고에 같은 원래 위치의 폴더가 있습니다. 먼저 금고에서 꺼내거나 이 폴더의 이름을 바꾸세요: {path}")
    return path


@one_at_a_time
def lock(path, password, others):
    """제자리에서 잠근다. 기록을 먼저 적고, 잠그기가 실패하면 기록을 지운다(잠겼는데 목록에 없는 폴더가 생기지 않게)."""
    verify(password)
    path = check_new(path, others)
    data = load()
    put(data, {"path": path, "open": False})
    try:
        seal(path)
    except OSError:
        put(load(), {"path": path})
        raise


@one_at_a_time
def set_vault(place, password):
    """금고를 place 안의 .vault로 정한다(만들고 잠근다). 금고에 든 것이 있으면 바꾸지 않는다."""
    verify(password)
    data = load()
    if any(f.get("id") for f in data["folders"]):
        raise ValueError("금고에 든 폴더를 모두 꺼낸 뒤에 금고를 바꿀 수 있습니다.")
    place = settings.folder_text(place, "금고 위치")
    vault = os.path.join(place, VAULT)
    os.makedirs(vault, exist_ok=True)
    if data["vault"] and exclude.key(data["vault"]) != exclude.key(vault) and os.path.isdir(data["vault"]):
        unseal(data["vault"])  # 빈 옛 금고는 잠금만 푼다(지우지 않는다).
    if not (data["vault"] and exclude.key(data["vault"]) == exclude.key(vault) and not data["vault_open"]):
        seal(vault)
    save({**data, "vault": vault, "vault_open": False})
    return vault


class Opened:
    """금고를 잠시 열었다가(잠겨 있었으면) 다시 잠근다."""

    def __init__(self, data):
        self.vault, self.was_sealed = data["vault"], not data["vault_open"]

    def __enter__(self):
        if self.was_sealed:
            unseal(self.vault)

    def __exit__(self, *error):
        if self.was_sealed:
            try:
                seal(self.vault)
            except OSError:
                save({**load(), "vault_open": True})  # 열린 채로 남았다고 적어 두어 close_all이 다시 잠그게 한다.
                raise


@one_at_a_time
def collect(path, password, others):
    """금고로 모은다: 같은 디스크의 금고 안으로 무작위 이름으로 옮긴다(이름 바꾸기라 복사하지 않는다). 기록을 먼저 적는다."""
    verify(password)
    data = load()
    if not data["vault"]:
        raise ValueError("먼저 설정에서 금고 위치를 정하세요.")
    path = check_new(path, others)
    if not drives.same_drive(path, data["vault"]):
        raise ValueError("금고와 다른 디스크의 폴더라 금고로 모을 수 없습니다. [잠그기]로 제자리에서 잠그세요.")
    item = {"path": path, "id": secrets.token_hex(8)}
    put(data, item)
    inside = False
    try:
        with Opened(data):
            os.rename(path, os.path.join(data["vault"], item["id"]))
            inside = True
    except OSError:
        if not inside:  # 옮긴 뒤 금고를 다시 잠그지 못한 것이면 기록은 남긴다(지우면 금고 안 무작위 이름 폴더의 주인을 잃는다).
            put(load(), {"path": path})
        raise


@one_at_a_time
def open_folder(path, password):
    """비밀번호가 맞으면 잠시 푼다. 금고 안의 것은 금고를 열고 그 폴더 경로를 돌려준다(창을 닫거나 [다시 잠그기]로 다시 잠근다)."""
    verify(password)
    data = load()
    found = record(data, path)
    if found.get("id"):
        if not data["vault_open"]:
            unseal(data["vault"])
            save({**data, "vault_open": True})
        return os.path.join(data["vault"], found["id"])
    unseal(found["path"])
    put(data, {"path": found["path"], "open": True})
    return found["path"]


@one_at_a_time
def close(path):
    """열어 둔 것을 다시 잠근다(비밀번호는 묻지 않는다). 금고 안의 것이면 금고를 잠근다."""
    data = load()
    found = record(data, path)
    if found.get("id"):
        seal(data["vault"])
        save({**data, "vault_open": False})
        return
    seal(found["path"])
    put(data, {"path": found["path"], "open": False})


@one_at_a_time
def take_out(path, password, folder=None):
    """금고에서 꺼낸다. 원래 위 폴더(또는 고른 folder)에 같은 이름이 있으면 '이름 (2)'. 위 폴더가 없으면 NoPlace."""
    verify(password)
    data = load()
    found = record(data, path)
    if not found.get("id"):
        raise ValueError(f"금고에 든 폴더가 아닙니다: {path}")
    parent = folder or os.path.dirname(found["path"])
    if not os.path.isdir(parent):
        raise NoPlace(f"원래 위 폴더가 없습니다. 꺼낼 폴더를 고르세요: {parent}")
    if not drives.same_drive(parent, data["vault"]):
        raise ValueError("금고와 같은 디스크의 폴더를 고르세요.")
    fence.refuse_system(fence.refuse(parent))  # Windows·게임 폴더 안으로도 꺼내지 않는다
    fence.refuse_system(fence.refuse(os.path.realpath(parent)))  # 정션이 가리키는 곳도
    target = fence.refuse(organize.free_name(parent, os.path.basename(found["path"]), set()))  # 지운 차단 폴더 자리에 꺼내지 않게
    with Opened(data):
        os.rename(os.path.join(data["vault"], found["id"]), target)
        put(load(), {"path": found["path"]})  # 금고를 다시 잠그기 전에 지운다(잠그다 실패해도 없는 폴더를 가리키는 기록이 남지 않게).
    return target


@one_at_a_time
def release(path, password):
    """제자리 잠금을 아주 푼다(목록에서 뺀다). 폴더가 사라졌으면(앱 밖에서 옮김·지움) 목록에서만 뺀다."""
    verify(password)
    data = load()
    found = record(data, path)
    if found.get("id"):
        raise ValueError("금고에 든 폴더는 [꺼내기]로 꺼내세요.")
    if os.path.isdir(found["path"]) and not found.get("open"):
        unseal(found["path"])
    put(data, {"path": found["path"]})


def refuse_clash(before, after, paths=None):
    """before를 after로 옮기면 그 안의 잠금 기록 위치가 다른 기록(금고에 든 폴더의 원래 위치 등)과 같아지는지. 같아지면 거부한다.
    기록은 위치로 찾으므로 둘이 같아지면 하나가 덮여, 금고 안 무작위 이름 폴더의 주인을 잃을 수 있다.
    paths: 미리 읽은 기록의 위치들(여러 개를 볼 때)."""
    old = {exclude.key(before)}
    paths = [f["path"] for f in load()["folders"]] if paths is None else paths
    inside = {exclude.key(after + p[len(before):]) for p in paths if fence.inside(p, old)}
    if inside & {exclude.key(p) for p in paths if not fence.inside(p, old)}:
        raise ValueError(f"옮기면 안의 잠금 폴더가 금고에 든 폴더의 원래 위치와 같아집니다. 금고에서 먼저 꺼내세요: {before}")


@one_at_a_time
def moved(pairs):
    """앱 안에서 옮기거나 이름을 바꾼 (전, 후) 폴더 안에 든 잠금 폴더(금고 안의 것은 꺼낼 원래 위치)·금고의 위치를 고친다."""
    data = load()
    changed = False
    for before, after in pairs:
        old = exclude.key(before)
        for item in data["folders"] + [data]:
            name = "vault" if item is data else "path"
            value = item.get(name)
            if not value or not fence.inside(value, {old}):
                continue
            item[name] = after + value[len(before):]
            changed = True
    if changed:
        save(data)


@one_at_a_time
def close_all():
    """열어 둔 잠금 폴더와 금고를 모두 다시 잠근다(창을 닫을 때와, 지난번에 비정상으로 끝났을 때를 위해 켤 때). 하나가 실패해도 나머지는 한다."""
    data = load()
    for f in data["folders"]:
        if f.get("open") and not f.get("id"):
            try:
                close(f["path"])
            except (OSError, ValueError):
                continue
    if data["vault_open"] and data["vault"]:
        try:
            seal(data["vault"])
            save({**load(), "vault_open": False})
        except OSError:
            pass

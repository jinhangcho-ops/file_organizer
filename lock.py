"""잠금 폴더: 비밀번호를 맞춰야 열리는 폴더. Windows 권한으로 가린다(암호화가 아니다).
잠그면 내 계정에 '거부' 권한(목록·읽기·쓰기·만들기·지나가기·지우기, 그 아래 전부)을 붙이고 숨김·시스템 속성을 켠다.
권한 보기·바꾸기는 거부하지 않는다. 그래서 관리자 권한 없이 다시 풀 수 있다(모든 권한(F)을 거부하면 관리자만 풀 수 있다).
비밀번호는 저장하지 않고 PBKDF2 해시만 설정 폴더의 lock.json에 둔다. 열어 둔 폴더는 창을 닫을 때 다시 잠근다."""
import ctypes
import hashlib
import hmac
import os
import secrets
import subprocess

import exclude
import fence
import settings

FILE = "lock.json"
ROUNDS = 200_000
MIN_LENGTH = 4
DENY = "(OI)(CI)(RD,WD,AD,X,DC,D)"
HIDE = 0x2 | 0x4  # 숨김 | 시스템
sid = [None]  # 지금 사용자의 SID(한 번 묻고 기억한다)


def load():
    data = settings.read(FILE, {})
    return {"hash": data.get("hash"), "salt": data.get("salt"), "folders": [f for f in data.get("folders", []) if isinstance(f, dict)]}


def folders():
    return [f["path"] for f in load()["folders"]]


def listing():
    """화면에 보여 줄 잠금 폴더들 [{"path", "open"}]."""
    return [{"path": f["path"], "open": bool(f.get("open"))} for f in load()["folders"]]


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


def set_password(new, old=None):
    """비밀번호를 정하거나 바꾼다. 이미 있으면 old가 맞아야 한다."""
    data = load()
    if data["hash"]:
        verify(old)
    if not isinstance(new, str) or len(new) < MIN_LENGTH:
        raise ValueError(f"비밀번호는 {MIN_LENGTH}자 이상으로 정하세요.")
    salt = secrets.token_hex(16)
    settings.write(FILE, {**data, "hash": digest(new, salt), "salt": salt})


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
    kernel = ctypes.windll.kernel32
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


def record(path):
    for f in load()["folders"]:
        if exclude.key(f["path"]) == exclude.key(path):
            return f
    raise ValueError(f"잠금 폴더가 아닙니다: {path}")


def mark(path, is_open):
    """path의 열림 표시를 바꾼다. is_open이 None이면 목록에서 뺀다."""
    data = load()
    kept = [f for f in data["folders"] if exclude.key(f["path"]) != exclude.key(path)]
    if is_open is not None:
        kept.append({"path": path, "open": is_open})
    settings.write(FILE, {**data, "folders": kept})


def lock(path, password, others):
    """폴더를 잠근다. others: 겹치면 안 되는 차단 폴더 key들."""
    verify(password)
    path = os.path.abspath(path)
    if not os.path.isdir(path) or os.path.dirname(path) == path:
        raise ValueError(f"잠글 수 있는 폴더가 아닙니다: {path}")
    if fence.overlaps(path, {exclude.key(p) for p in folders()} | set(others)):
        raise ValueError(f"이미 잠갔거나 차단한 폴더와 겹칩니다: {path}")
    seal(path)
    mark(path, False)


def open_folder(path, password):
    """비밀번호가 맞으면 잠시 푼다(창을 닫거나 [다시 잠그기]를 누르면 다시 잠근다)."""
    verify(password)
    path = record(path)["path"]
    unseal(path)
    mark(path, True)
    return path


def close(path):
    """열어 둔 폴더를 다시 잠근다(비밀번호는 묻지 않는다)."""
    path = record(path)["path"]
    seal(path)
    mark(path, False)


def release(path, password):
    """잠금을 아주 푼다(목록에서 뺀다). 다음 색인부터 다시 들어간다."""
    verify(password)
    found = record(path)
    if not found.get("open"):
        unseal(found["path"])
    mark(found["path"], None)


def close_all():
    """창을 닫을 때: 열어 둔 잠금 폴더를 모두 다시 잠근다. 하나가 실패해도 나머지는 한다."""
    for f in load()["folders"]:
        if f.get("open"):
            try:
                close(f["path"])
            except (OSError, ValueError):
                continue

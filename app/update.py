"""새 버전 알림과 [지금 업데이트]. GitHub에는 최신 릴리스 주소(넘어간 주소에서 태그를 읽는다)와, 새 버전일 때 그 릴리스 노트만 묻는다.
[지금 업데이트]는 설치 파일과 지문(.sha256)을 받아 지문이 같을 때만 조용한 설치(/SILENT … /RELAUNCH)로 띄운다."""
import configparser
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from catalog import index
from app import jobs

REPO = "https://github.com/jinhangcho-ops/file_organizer"
if not getattr(sys, "frozen", False):  # 화면 확인(소스 실행)만 가짜 주소를 쓴다. 설치한 exe는 환경 변수로 받는 곳을 바꿀 수 없다.
    REPO = os.environ.get("FILE_ORGANIZER_UPDATE_REPO") or REPO
SETUP = "file_organizer_setup.exe"
TAG = re.compile(r"v[0-9A-Za-z.+-]+")
LIMITS = {"notes": 50_000, "sha256": 1_000, "setup": 50_000_000}  # 받는 크기 상한(바이트)
NEED = 1_000_000_000  # 설치 드라이브에 있어야 할 빈 공간: 앱 파일 zip(약 150MB)과 푼 것(약 360MB), 옛 판을 지우기 전까지 둘 다
TIMEOUT = 15  # 초
asked = {}  # 이번 실행에서 물은 답(실행마다 한 번만 묻는다)
# 뮤텍스는 앱이 끝날 때까지 쥔다. 설치가 /RELAUNCH면 InitializeSetup에서 앱이 끝나기를 기다린다(installer.iss).
# 놓고 띄우면 앱이 아직 켜진 동안 설치가 쓰고 있는 _internal을 지우고 덮어썼다.


def installed():
    """설치 파일이 {app}\\version.ini에 적어 둔 버전. 개발 실행·기록 없음·태그 없이 만든 시험 빌드(0.0.0)는 None."""
    if not getattr(sys, "frozen", False):
        return None
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(sys.executable), "version.ini"), encoding="utf-8")
    version = config.get("app", "version", fallback="")
    return version if numbers(version) and version != "0.0.0" else None


def numbers(version):
    """'1.10.0' → (1, 10, 0). 숫자가 아닌 조각이 있으면 None(견주지 않는다)."""
    parts = version.split(".") if isinstance(version, str) else []
    return tuple(map(int, parts)) if parts and all(p.isdigit() for p in parts) else None


def get(url, limit):
    """url의 내용(limit 바이트까지). 넘으면 ValueError."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as answer:
        data = answer.read(limit + 1)
    if len(data) > limit:
        raise ValueError("받을 파일이 너무 큽니다.")
    return data


def latest_tag():
    """releases/latest가 넘어간 주소(…/releases/tag/v1.2.3)의 마지막 조각. 모양이 다르면 None."""
    with urllib.request.urlopen(REPO + "/releases/latest", timeout=TIMEOUT) as answer:
        url = answer.geturl()
    page = REPO + "/releases/tag/"  # 와이파이 로그인 페이지·프록시가 다른 곳으로 넘기면 태그가 아니다
    tag = urllib.parse.unquote(url[len(page):].rstrip("/")) if url.startswith(page) else ""
    return tag if TAG.fullmatch(tag) else None


def check(on):
    """{"current", "version", "notes"}(새 버전이 있을 때, 노트를 못 받으면 notes는 None) 또는 None. on: 설정 update_check."""
    if not on or not (current := installed()):
        return None
    if "answer" not in asked:
        asked["answer"] = None
        try:
            tag = latest_tag()
        except (OSError, ValueError):  # 인터넷·프록시 오류는 조용히 넘긴다
            tag = None
        new = numbers(tag[1:]) if tag else None
        if new and new > numbers(current):
            try:
                notes = get(f"{REPO}/releases/download/{tag}/RELEASE_NOTES.md", LIMITS["notes"]).decode("utf-8", "replace")
            except (OSError, ValueError):
                notes = None
            asked["answer"] = {"current": current, "version": tag[1:], "notes": notes}
    return asked["answer"]


def refuse():
    """받기 전에 거절할 까닭(없으면 None)."""
    if not installed():
        return "설치한 프로그램에서만 업데이트할 수 있어요."
    if jobs.status["running"] or index.status["running"]:
        return "작업이나 색인이 끝난 뒤에 업데이트하세요."
    if shutil.disk_usage(os.path.dirname(sys.executable)).free < NEED:
        return f"설치 드라이브에 빈 공간이 모자라요({NEED // 1_000_000_000}GB 필요)."
    return None


def install(version):
    """그 버전의 설치 파일을 받아 지문이 같으면 조용한 설치를 띄운다. 2초 안에 실패 코드로 끝나면 ValueError(프로그램은 켜 둔다)."""
    if not isinstance(version, str) or not numbers(version):
        raise ValueError("버전 글자가 잘못됐어요.")
    if version != (asked.get("answer") or {}).get("version"):  # 이번 실행에서 확인한 새 버전만(옛 판으로 되돌리기 막기)
        raise ValueError("확인한 새 버전만 설치할 수 있어요.")
    # 시작한 순간부터 새 작업을 받지 않는다(jobs.closing): 받는 동안 시작한 작업은 띄운 뒤의 강제 종료에 끊긴다. 두 번째 요청도 여기서 막힌다.
    with jobs.lock:
        if jobs.closing:
            raise ValueError("이미 업데이트를 시작했거나 프로그램을 닫는 중이에요.")
        if reason := refuse():
            raise ValueError(reason)
        jobs.closing = True
    try:
        return launch(version)
    except BaseException:
        jobs.closing = False
        raise


def launch(version):
    base = f"{REPO}/releases/download/v{version}/{SETUP}"
    setup = get(base, LIMITS["setup"])
    want = get(base + ".sha256", LIMITS["sha256"]).decode("ascii", "replace").split()[:1]
    if not want or hashlib.sha256(setup).hexdigest() != want[0].lower():
        raise ValueError("받은 설치 파일의 지문(SHA-256)이 올린 것과 달라 설치하지 않았어요.")
    folder = tempfile.mkdtemp(prefix="file_organizer_update_")  # 설치 파일이 도는 동안 써야 해서 지우지 않는다(이 작업이 만든 폴더)
    path = os.path.join(folder, SETUP)
    with open(path, "wb") as out:
        out.write(setup)
    process = subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL",
                                f"/DIR={os.path.dirname(sys.executable)}", "/RELAUNCH"])
    try:
        code = process.wait(2)
    except subprocess.TimeoutExpired:
        code = 0  # 앱이 끝나기를 기다리는 중
    if code:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(f"설치를 시작하지 못했어요(코드 {code}).")
    return path

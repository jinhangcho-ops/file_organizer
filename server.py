"""화면이 부르는 Flask 서버. desktop.py가 127.0.0.1의 빈 포트에 띄우고, 열쇠(KEY)를 아는 프로그램 창의 요청만 받는다."""
import os
import secrets
import subprocess

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import actions
import archive
import blacklist
import drives
import exclude
import fence
import index
import jobs
import kinds
import lock
import organize
import search
import settings
import tree

app = Flask(__name__, static_folder="static")
KEY = secrets.token_urlsafe(32)  # 실행할 때마다 새로 만든다. desktop.py가 창의 첫 주소에 넣는다.
ask_folder = None  # 폴더 고르는 대화상자(→ 경로 또는 None). desktop.py가 채운다.
window_control = None  # 제목 줄 없는 창의 버튼·크기 조절(동작, 값 → 답). desktop.py가 채운다.
WINDOW_ACTIONS = {"minimize", "toggle", "close", "bounds", "place"}
CSP = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:"


@app.before_request
def only_window():
    """창은 첫 주소의 key로 들어와 쿠키를 받고, 그 뒤로는 쿠키로 확인한다. 열쇠가 없으면 화면·파일·API 모두 403."""
    given = request.args.get("key") or request.cookies.get("key", "")
    if not secrets.compare_digest(given.encode(), KEY.encode()):
        abort(403, "프로그램 창에서만 열 수 있습니다.")


@app.after_request
def headers(response):
    response.headers["Content-Security-Policy"] = CSP
    return response


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    if isinstance(e, OSError):
        return jsonify(error=f"{e.strerror or e}: {e.filename or ''}".rstrip(": ")), 400
    if isinstance(e, ValueError):
        return jsonify(error=str(e)), 400
    raise e


@app.get("/")
def page():
    response = send_from_directory(app.static_folder, "index.html")
    response.set_cookie("key", KEY, httponly=True, samesite="Strict")
    return response


def body():
    return request.get_json(silent=True) or {}


@app.get("/api/state")
def state():
    return jsonify(settings=settings.load(), drives=drives.list_drives(), known=drives.known_folders(), kinds=kinds.NAMES,
                   last_access=drives.last_access_on(), undo=len(organize.last()), index=index.status, counts=search.counts(),
                   places=tree.places(), exclude=exclude.load(), blacklist=blacklist.load(), admin=blacklist.is_admin(),
                   locks=lock.listing(), lock_password=lock.has_password(), panes=settings.PANES)


@app.post("/api/settings")
def save_settings():
    return jsonify(settings=settings.save(body()))


def pick_folder():
    if ask_folder is None:
        raise ValueError("폴더 고르기는 프로그램 창에서만 됩니다.")
    return ask_folder()


@app.post("/api/pick")
def pick():
    return jsonify(path=pick_folder())


@app.post("/api/organize/preview")
def preview():
    data = body()
    settings.save({"source": data.get("source"), "dest": data.get("dest")})  # 고른 곳을 기억한다(검사도 여기서).
    return jsonify(moves=organize.plan(data["source"], data["dest"]))


@app.post("/api/organize/run")
def organize_run():
    data = body()
    return jsonify(jobs.start("정리", lambda progress: organize.run(data.get("source"), data.get("dest"), progress)))


@app.post("/api/organize/undo")
def organize_undo():
    return jsonify(jobs.start("되돌리기", organize.undo))


@app.get("/api/job")
def job():
    """뒤에서 도는 작업(정리, 옮기기, 휴지통, 보관)의 진행. 화면이 끝날 때까지 읽는다."""
    return jsonify(jobs.status)


@app.post("/api/chip")
def chip():
    """종류 칩을 누른 횟수를 센다(많이 누른 칩을 왼쪽에)."""
    return jsonify(settings=settings.count_chip(body().get("kind")))


@app.post("/api/archive/preview")
def archive_preview():
    data = body()
    config = settings.save({"archive_dest": data.get("dest"), "cold_days": data.get("days")})
    return jsonify(archive.preview(config["cold_days"], config["archive_dest"]))


@app.post("/api/archive/run")
def archive_run():
    data = body()
    if data.get("confirm") is not True:
        raise ValueError("확인한 뒤에만 보관합니다.")
    config = settings.save({"archive_dest": data.get("dest"), "cold_days": data.get("days")})
    return jsonify(jobs.start("보관", lambda progress: archive.run(config["archive_dest"], config["cold_days"], progress)))


@app.route("/api/index", methods=["GET", "POST"])
def build_index():
    if request.method == "POST":
        paths = index.roots(settings.load())
        if not paths:
            raise ValueError("색인할 폴더가 없습니다.")
        if not index.start(paths):
            raise ValueError("이미 색인하는 중입니다.")
    return jsonify(index.status)


@app.post("/api/index/stop")
def stop_index():
    """돌고 있는 색인을 지금 파일까지만 하고 멈추게 한다. 돌고 있지 않으면 아무 일도 하지 않는다."""
    index.stop.set()
    return jsonify(index.status)


@app.post("/api/files")
def files():
    return jsonify(search.listing(body()))


@app.get("/api/chain")
def chain():
    """파일(folder=1이면 그 폴더 자신)까지의 폴더들."""
    return jsonify(folders=tree.chain(request.args["path"], request.args.get("folder") == "1"))


@app.get("/api/same")
def same():
    """상세에 보여 줄 내용이 같은 파일과 이름이 같은 파일."""
    return jsonify(search.same(checked(request.args.get("path", ""))))


@app.get("/api/preview")
def file_preview():
    """상세의 내용 미리보기(색인이 읽어 둔 글자의 앞부분)."""
    return jsonify(search.preview(checked(request.args.get("path", ""))))


@app.post("/api/window")
def window():
    """화면이 그린 제목 줄의 버튼(최소화·최대화·닫기)과 가장자리 크기 조절. bounds는 지금 창의 위치·크기, place는 위치·크기 바꾸기."""
    data = body()
    action = data.get("action")
    if action not in WINDOW_ACTIONS:
        raise ValueError(f"모르는 창 동작입니다: {action}")
    if window_control is None:
        raise ValueError("프로그램 창에서만 됩니다.")
    return jsonify(window_control(action, data) or {})


@app.get("/api/tree")
def folder_tree():
    """폴더의 바로 아래 폴더들. 왼쪽 폴더 나무가 펼칠 때 부른다."""
    return jsonify(folders=tree.children(request.args["path"]))


@app.post("/api/exclude")
def change_exclude():
    data = body()
    return jsonify(exclude=exclude.remove(data["path"]) if data.get("remove") else exclude.add(data["path"]))


def checked(path):
    """색인에 있고 지금도 있는 파일·폴더만 받는다(화면이 다루는 파일은 모두 색인에서 왔다). 차단·잠금 폴더 안은 거부한다."""
    if not isinstance(path, str) or not index.known(path) or not os.path.exists(path):
        raise ValueError(f"색인에 없는 경로입니다. 색인을 갱신하세요: {path}")
    return fence.refuse(path)


def indexed_path():
    return checked(body().get("path") or "")


def indexed_paths(movable=True):
    """요청의 paths(여러 개). movable이면 색인한 맨 위 폴더는 받지 않는다(통째로 옮기거나 지우지 않는다)."""
    paths = body().get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("고른 파일이 없습니다.")
    for path in paths:
        checked(path)
        if movable and index.is_root(path):
            raise ValueError(f"색인한 맨 위 폴더는 옮기거나 지울 수 없습니다: {path}")
    return list(dict.fromkeys(paths))


@app.post("/api/move")
def move():
    """파일·폴더들을 folder(나무에 끌어 놓은 폴더)로 옮긴다. folder가 없으면 폴더 고르기 창으로 묻고, 고르지 않으면 job이 null."""
    paths = indexed_paths()
    folder = body().get("folder")
    if folder:
        checked(folder)
    else:
        folder = pick_folder()
        if not folder:
            return jsonify(job=None)
        fence.refuse(folder)
    return jsonify(job=jobs.start("옮기기", lambda progress: actions.move_many(paths, folder, progress)))


@app.post("/api/rename")
def rename():
    return jsonify(path=actions.rename(indexed_path(), body().get("name")))


@app.post("/api/trash")
def trash():
    """파일·폴더들을 휴지통으로. 화면의 확인 창에서 [확인]을 눌러 confirm이 true일 때만 한다."""
    paths = indexed_paths()
    if body().get("confirm") is not True:
        raise ValueError("확인한 뒤에만 휴지통으로 보냅니다.")
    return jsonify(job=jobs.start("휴지통으로 보내기", lambda progress: actions.trash_many(paths, progress)))


@app.post("/api/blacklist")
def change_blacklist():
    """차단 폴더 넣기·빼기(관리자 권한으로 실행했을 때만). 넣으면 그 아래의 색인 기록을 바로 지운다(실제 파일은 그대로)."""
    data = body()
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("폴더를 고르세요.")
    if data.get("remove"):
        return jsonify(blacklist=blacklist.remove(path))
    paths = blacklist.add(path, {exclude.key(p) for p in lock.folders()})
    index.removed([path])
    return jsonify(blacklist=paths)


@app.get("/api/blacklist/candidates")
def blacklist_candidates():
    """색인하는 드라이브에서 찾은 게임 폴더(아직 차단하지 않은 것)."""
    return jsonify(folders=blacklist.candidates(index.roots(settings.load())))


@app.post("/api/lock/password")
def lock_password():
    """잠금 비밀번호를 정하거나 바꾼다(바꿀 때는 지금 비밀번호가 맞아야 한다)."""
    data = body()
    lock.set_password(data.get("new"), data.get("old"))
    return jsonify(lock_password=True)


@app.post("/api/lock")
def lock_folder():
    """색인에 있는 폴더를 잠근다. 색인한 맨 위 폴더는 잠그지 않는다. 잠그면 그 아래의 색인 기록을 지운다."""
    data = body()
    path = indexed_path()
    if not os.path.isdir(path) or index.is_root(path):
        raise ValueError(f"잠글 수 없는 폴더입니다(색인한 맨 위 폴더나 파일): {path}")
    lock.lock(path, data.get("password"), {exclude.key(p) for p in blacklist.load()})
    index.removed([path])
    return jsonify(locks=lock.listing())


@app.post("/api/lock/open")
def lock_open():
    """비밀번호가 맞으면 잠금 폴더를 잠시 풀고 탐색기로 연다."""
    data = body()
    path = lock.open_folder(data.get("path"), data.get("password"))
    os.startfile(path)
    return jsonify(locks=lock.listing())


@app.post("/api/lock/close")
def lock_close():
    lock.close(body().get("path"))
    return jsonify(locks=lock.listing())


@app.post("/api/lock/release")
def lock_release():
    """잠금을 아주 푼다. 다음 색인부터 다시 목록에 나온다."""
    data = body()
    lock.release(data.get("path"), data.get("password"))
    return jsonify(locks=lock.listing())


@app.post("/api/open")
def open_path():
    """색인에 있는 파일·폴더만 연다. reveal이면 탐색기에서 그 파일을 골라 보여 준다."""
    data = body()
    path = indexed_path()
    if data.get("reveal"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.startfile(path)
    return jsonify(ok=True)

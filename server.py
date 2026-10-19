"""화면이 부르는 Flask 서버. desktop.py가 127.0.0.1의 빈 포트에 띄우고, 열쇠(KEY)를 아는 프로그램 창의 요청만 받는다."""
import os
import secrets
import subprocess

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import actions
import archive
import drives
import exclude
import index
import jobs
import kinds
import organize
import search
import settings
import tree

app = Flask(__name__, static_folder="static")
KEY = secrets.token_urlsafe(32)  # 실행할 때마다 새로 만든다. desktop.py가 창의 첫 주소에 넣는다.
ask_folder = None  # 폴더 고르는 대화상자(→ 경로 또는 None). desktop.py가 채운다.
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
                   places=tree.places(), exclude=exclude.load())


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


@app.get("/api/tree")
def folder_tree():
    """폴더의 바로 아래 폴더들. 왼쪽 폴더 나무가 펼칠 때 부른다."""
    return jsonify(folders=tree.children(request.args["path"]))


@app.post("/api/exclude")
def change_exclude():
    data = body()
    return jsonify(exclude=exclude.remove(data["path"]) if data.get("remove") else exclude.add(data["path"]))


def checked(path):
    """색인에 있고 지금도 있는 파일·폴더만 받는다(화면이 다루는 파일은 모두 색인에서 왔다)."""
    if not isinstance(path, str) or not index.known(path) or not os.path.exists(path):
        raise ValueError(f"색인에 없는 경로입니다. 색인을 갱신하세요: {path}")
    return path


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

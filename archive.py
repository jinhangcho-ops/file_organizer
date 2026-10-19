"""오래된 파일 보관: 연 시각과 수정 시각 중 늦은 쪽이 cold_days(기본 365일)보다 오래된 파일을 보관할 곳의 zip 하나로 묶고,
zip을 다시 열어 CRC와 크기를 확인한 뒤에만 원본을 휴지통으로 보낸다(trash). 영구 삭제는 하지 않는다.
- zip 안의 경로는 원래 경로를 살린다(C:\\Users\\a.docx → C/Users/a.docx).
- 이미 압축된 형식(압축, 동영상, 음악, 사진 등)은 다시 압축하지 않고 그대로 넣는다(ZIP_STORED).
- 설치파일(exe 등), 보관할 곳 안의 파일, 예외로 지정한 곳, 휴지통으로 보낼 수 없는 곳(trash.problem)의 파일은 넣지 않는다.
  보낼 수 없는 파일을 넣으면 zip과 원본이 둘 다 남아 자리만 더 차지하기 때문이다."""
import datetime
import os
import time
import zipfile

import exclude
import filetime
import index
import kinds
import organize
import settings
import trash

DAY = 86400
SKIP_KINDS = {"설치파일"}
STORED_KINDS = {"압축", "동영상", "음악", "사진"}
STORED_EXTS = {".png", ".gif", ".webp", ".docx", ".xlsx", ".pptx", ".hwpx", ".odt", ".ods", ".odp"}  # 이미 zip으로 압축된 형식들


def candidates(days, dest=""):
    """보관할 파일들: [{"path", "name", "kind", "size", "stamp"}], 오래된 순."""
    rule, values = exclude.where(exclude.load())
    since = int(time.time()) - days * DAY
    parts = ["max(f.accessed, f.modified) < ?", f"f.kind not in ({', '.join('?' * len(SKIP_KINDS))})"] + ([rule] if rule else [])
    db = index.connect()
    try:
        rows = db.execute(f"select f.path, f.name, f.kind, f.size, f.stamp from files f where {' and '.join(parts)} order by max(f.accessed, f.modified)",
                          [since, *SKIP_KINDS, *values]).fetchall()
    finally:
        db.close()
    inside = os.path.join(exclude.key(dest), "") if dest else None
    return [dict(row) for row in rows if not (inside and exclude.key(row["path"]).startswith(inside))]


def preview(days, dest=""):
    """종류별 개수·용량과 합계. 휴지통으로 보낼 수 없는 곳의 파일은 따로 센다."""
    groups, blocked = {}, 0
    for row in candidates(days, dest):
        if trash.problem(row["path"]):
            blocked += 1
            continue
        count, size = groups.get(row["kind"], (0, 0))
        groups[row["kind"]] = (count + 1, size + row["size"])
    by_kind = [[name, *groups[name]] for name in kinds.NAMES if name in groups]
    return {"kinds": by_kind, "files": sum(g[0] for g in groups.values()), "bytes": sum(g[1] for g in groups.values()), "blocked": blocked}


def arcname(path):
    """zip 안의 이름: 원래 경로를 살린다(드라이브 글자는 폴더 이름으로)."""
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return (drive.rstrip(":") + rest).replace("\\", "/")


def verify(zip_path, written):
    """zip을 다시 열어 모든 파일의 CRC를 확인하고, 이름과 크기가 넣은 것과 같은지 본다."""
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            return False
        sizes = {info.filename: info.file_size for info in archive.infolist()}
    return all(sizes.get(arcname(row["path"])) == row["size"] for row in written)


def run(dest, days, progress=lambda *args: None):
    """보관한다. {"zip", "files", "original", "zip_size", "freed", "failed": [{"from", "error"}]}"""
    dest = settings.folder_text(dest, "보관할 곳")
    rows = [row for row in candidates(days, dest) if not trash.problem(row["path"])]
    if not rows:
        raise ValueError("보관할 파일이 없습니다.")
    zip_path = organize.free_name(dest, datetime.datetime.now().strftime("%Y-%m-%d_%H%M") + ".zip", set())
    written, failed = [], []
    progress(0, len(rows) * 2, "zip 만들기")
    with zipfile.ZipFile(zip_path, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for n, row in enumerate(rows):
            progress(n, None, row["path"])
            try:
                st = os.stat(row["path"])
                if st.st_mtime_ns != row["stamp"] or st.st_size != row["size"]:  # 색인한 뒤에 바뀌었다(오래된 파일이 아니게 됐다).
                    raise ValueError("색인한 뒤에 바뀐 파일이라 넣지 않았습니다.")
                stored = row["kind"] in STORED_KINDS or os.path.splitext(row["path"])[1].lower() in STORED_EXTS
                archive.write(row["path"], arcname(row["path"]), zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
                filetime.restore_access(row["path"], st)  # zip이 잘못돼 원본을 남기게 되면 '오래된 파일'인 채로 남게
                written.append(row)
            except (OSError, ValueError) as e:
                failed.append({"from": row["path"], "error": getattr(e, "strerror", None) or str(e)})
    progress(len(rows), None, "zip 확인")
    if not written or not verify(zip_path, written):
        trash.to_trash([zip_path])  # 잘못 만든 zip도 영구 삭제하지 않는다. 원본은 그대로 둔다.
        raise ValueError("zip을 확인하지 못해 원본은 그대로 두었습니다." if written else "zip에 넣은 파일이 없습니다.")
    sent = []
    for n, row in enumerate(written):  # 확인한 뒤에만 원본을 휴지통으로
        progress(len(rows) + n, None, row["path"])
        ok, refused = trash.to_trash([row["path"]])
        sent += ok
        failed += [{"from": p, "error": reason} for p, reason in refused]
    progress(len(rows) * 2, None)
    if sent:
        index.removed(sent)
    sizes = {row["path"]: row["size"] for row in written}
    return {"zip": zip_path, "files": len(written), "original": sum(sizes.values()), "zip_size": os.path.getsize(zip_path),
            "freed": sum(sizes[p] for p in sent), "failed": failed}

"""상세의 '받은 곳·편집한 사람': 파일을 고를 때 그 파일에서 바로 읽는다(색인에 저장하지 않는다).
- 받은 곳: 브라우저가 붙인 Zone.Identifier의 HostUrl(받은 주소)·ReferrerUrl(링크가 있던 페이지). 주소의 ? 뒤와 아이디·비밀번호는 가린다.
- 만든 사람·마지막 저장한 사람: Office 문서(docx·xlsx·pptx)의 docProps/core.xml. 그 사람의 Office에 설정된 이름이다.
- 작성자: PDF의 정보(Author).
- 소유자: 파일의 Windows 소유자 계정(보통 처음 만든 사람, 고친 사람은 아니다).
읽은 뒤 연 시각은 되돌린다('오래 안 본 파일'이 바뀌지 않게)."""
import ctypes
import os
import zipfile
from ctypes import wintypes
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import filetime

OFFICE = (".docx", ".xlsx", ".xlsm", ".pptx")
CORE_MAX = 1024 * 1024  # 문서 정보 파일이 이보다 크면 읽지 않는다(이상한 파일).
NS = {"dc": "http://purl.org/dc/elements/1.1/", "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"}


def masked(url):
    """주소에서 아이디·비밀번호와 ? 뒤를 가린다. 주소 모양이 아니면 ''."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port  # 범위 밖의 포트(:99999)도 ValueError
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    host = parts.hostname + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, host, parts.path, "", "")) + ("?…" if parts.query else "")


def zone(path):
    """Zone.Identifier의 (HostUrl, ReferrerUrl), 가린 주소로. 없으면 ('', '')."""
    try:
        with open(path + ":Zone.Identifier", encoding="utf-8", errors="replace") as stream:
            lines = stream.read(64 * 1024).splitlines()
    except OSError:
        return "", ""
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    return masked(values.get("HostUrl", "")), masked(values.get("ReferrerUrl", ""))


def office_people(path):
    """Office 문서의 (만든 사람, 마지막 저장한 사람). 열지 못하면 ('', '')."""
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("docProps/core.xml")
            if info.file_size > CORE_MAX:
                return "", ""
            root = ElementTree.fromstring(archive.read(info))
    except Exception:  # 깨진 문서: 압축 방식(deflate·lzma·zstd…)마다 오류가 달라 모두 받는다
        return "", ""
    return (root.findtext("dc:creator", "", NS) or "").strip(), (root.findtext("cp:lastModifiedBy", "", NS) or "").strip()


def pdf_author(path):
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(path)
    except (pypdfium2.PdfiumError, OSError):
        return ""
    try:
        return (document.get_metadata_dict().get("Author") or "").strip()
    finally:
        document.close()


advapi = ctypes.WinDLL("advapi32", use_last_error=True)
advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
advapi.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_int)]
ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def owner(path):
    """파일의 Windows 소유자('도메인\\계정'). 모르면 ''."""
    sid, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    if advapi.GetNamedSecurityInfoW(path, 1, 1, ctypes.byref(sid), None, None, None, ctypes.byref(descriptor)):  # 1: 파일, 1: 소유자
        return ""
    try:
        name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        size_name, size_domain, kind = wintypes.DWORD(256), wintypes.DWORD(256), ctypes.c_int()
        if not advapi.LookupAccountSidW(None, sid, name, ctypes.byref(size_name), domain, ctypes.byref(size_domain), ctypes.byref(kind)):
            return ""
        return f"{domain.value}\\{name.value}" if domain.value else name.value
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)


def info(path):
    """{"source", "referrer", "author", "last_saved_by", "owner"}(없는 것은 '')."""
    st = os.stat(path)
    ext = os.path.splitext(path)[1].lower()
    source, referrer = zone(path)
    author, last_saved_by = office_people(path) if ext in OFFICE else ("", "")
    if ext == ".pdf":
        author = pdf_author(path)
    filetime.restore_access(path, st)
    return {"source": source, "referrer": referrer, "author": author, "last_saved_by": last_saved_by, "owner": owner(path)}

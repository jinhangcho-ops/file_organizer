"""상세 칸의 내 태그(#코딩): 화면 글자(static/app.js, app.css)에 필요한 부분이 있는지와 app.js 문법을 본다."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")


def body(name):
    """function name(...) { ... } 의 본문(다음 줄 시작의 '}'까지)."""
    m = re.search(r"function " + name + r"\(.*?\n\}", JS, re.S)
    assert m, name
    return m.group(0)


def test_show_detail_calls_show_tags():
    assert "showTags(row, ticket)" in body("showDetail")


def test_tag_paths_and_placeholder():
    tags = body("showTags")
    for path in ('"/api/tags?path="', '"/api/tags/remove"', '"/api/tags"'):
        assert path in tags
    assert "#태그 추가" in tags and "maxLength: 50" in tags
    assert "selectedPaths()" in tags and "stopPropagation" in tags


def test_tag_name_user_branch_splits_on_first_colon():
    tag_name = body("tagName")
    assert 'kindName === "user"' in tag_name and 'indexOf(":")' in tag_name


def test_title_and_css():
    assert 'scope.tag.startsWith("user:")' in body("showFiles")
    assert ".pill-x" in (STATIC / "app.css").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="node 없음")
def test_app_js_syntax():
    done = subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr

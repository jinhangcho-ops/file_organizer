"use strict";
// 화면: 제목 줄(직접 그림) · 도구 줄 · 왼쪽 폴더 목록 · 가운데 파일 표 · 오른쪽 상세(파일을 고를 때만). 설정·정리·보관은 본문 전체를 바꿔 연다.
// 목록에서 여러 줄을 고르고(체크·Ctrl·Shift·끌어서 사각형) 왼쪽 폴더나 위쪽 휴지통에 끌어 놓을 수 있다. 서버는 server.py.

let state = null;                 // /api/state의 답
let page = "files";               // files, organize, archive, setup
let scope = {all: true};          // 목록의 범위: {all} | {folder, name}
let trail = [];                   // 지나온 범위(빈 곳 오른쪽 클릭으로 돌아간다)
let filter = "";                  // 고른 필터 칩(VIEWS의 이름, "" = 없음)
let kind = "";                    // 고른 종류 칩("" = 전체)
let ext = "";                     // 고른 확장자 칩("" = 그 종류 모두). 종류를 골랐을 때만 나온다.
let tag = "";                     // 고른 추정 칩(search.tag_condition, "" = 없음). 사진·이미지·동영상을 골랐을 때만 나온다.
let sort = "date", order = "desc";
let more = {};                    // [조건]의 값
let picked = null;                // 상세에 보이는 파일(목록의 줄)
let menuPath = null;              // 오른쪽 클릭한 파일
let source = "", dest = "", archiveDest = "";
let listed = 0;                   // 늦게 온 목록 응답을 버리기 위한 번호
let rows = [];                    // 지금 목록의 줄
let anchor = -1;                  // Shift로 범위를 고를 때의 시작 줄
const selected = new Map();       // 고른 줄: 경로(소문자) → 줄
const opened = new Set();         // 펼쳐 둔 폴더(소문자 경로). 다시 그려도 펼친 채로 둔다.
const children = new Map();       // 읽어 둔 하위 폴더: 경로(소문자) → [폴더]

const VIEWS = {  // 위쪽 필터 칩이자 설정 화면의 숫자 카드(이 순서). warn: 정리할 거리라 숫자를 경고 색으로.
  // 날짜 열의 내용은 서버가 고른다(받은 파일은 받은 날짜, 안 본 파일은 마지막으로 연 날짜, 그 밖은 수정한 날짜).
  duplicates: {title: "중복 의심", warn: true},
  downloaded: {title: "최근 다운로드"},
  modified: {title: "최근 수정"},
  stale: {title: () => `${state.settings.stale_days}일 넘게 안 본 파일`, warn: true},
};
const PAGES = {files: "파일 정리", setup: "설정", organize: "다운로드 정리하기", archive: "오래된 파일 보관", games: "게임"};
// 같은 파일의 까닭(search.SAME)과 태그 글자. 태그를 누르면 그 파일과 같은 파일들만 경로와 함께 본다.
const WHY = {bytes: "중복 의심", doc: "내용 같은 문서", text: "글자 같은 문서", media: "보이는 내용 같음"};

// ---------- 도움 함수(요청·글자·크기·대화상자·고르기 창은 common.js) ----------
const lower = (path) => path.toLowerCase();
const two = (n) => String(n).padStart(2, "0");
const baseName = (path) => path.slice(path.lastIndexOf("\\") + 1);
const dirName = (path) => path.slice(0, path.lastIndexOf("\\"));
const extOf = (name) => (name.includes(".") ? name.slice(name.lastIndexOf(".")) : "");
const viewTitle = (view) => (typeof VIEWS[view].title === "function" ? VIEWS[view].title() : VIEWS[view].title);

function shortDate(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000);
  return d.getFullYear() === new Date().getFullYear() ? `${d.getMonth() + 1}월 ${d.getDate()}일` : `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}.`;
}

function fullDate(seconds) {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}. ${two(d.getMonth() + 1)}. ${two(d.getDate())}. ${two(d.getHours())}:${two(d.getMinutes())}`;
}

// 조사: 이름 끝 글자에 받침이 있으면 앞의 것(을, 으로), 없으면 뒤의 것(를, 로). 한글이 아니면 둘 다 적는다.
function josa(word, withFinal, without) {
  const code = word.charCodeAt(word.length - 1) - 0xac00;
  if (code < 0 || code > 11171) return `${withFinal}(${without})`;
  const final = code % 28;
  if (withFinal === "으로") return final && final !== 8 ? "으로" : "로";  // ㄹ 받침은 '로'
  return final ? withFinal : without;
}

function failedText(result) {
  const failed = result.failed.filter((f) => f.from !== "색인");  // 색인 줄은 못 한 파일이 아니다(파일은 다 했고 기록만 못 고쳤다): 따로 적는다.
  const notes = result.failed.filter((f) => f.from === "색인").map((f) => `\n색인: ${f.error}`).join("");
  return (failed.length ? `\n${failed.length}개는 못 했어요.\n` + failed.map((f) => `${f.from}: ${f.error}`).join("\n") : "") + notes;
}

// 뒤에서 도는 작업(정리·옮기기·휴지통·보관): 시작 요청의 답을 받아 끝날 때까지 아래 진행률 막대에 보여 주고, 결과를 돌려준다.
let jobsStarted = 0;  // 이 화면이 기다리기 시작한 작업 수(leaveGoneFolder가 묻는 사이에 새 작업이 끼었는지 본다)

async function runJob(started) {
  if (!started) return null;
  jobsStarted++;
  const box = $("job");
  box.classList.remove("hidden", "done");
  let status = started;
  while (status.running) {
    const percent = status.total ? Math.round(status.done / status.total * 100) : 0;
    $("job-text").textContent = `${status.name} 중… ${status.total ? `${status.done.toLocaleString()} / ${status.total.toLocaleString()}` : ""}`;
    $("job-bar").style.width = percent + "%";
    await new Promise((resolve) => setTimeout(resolve, 400));
    status = await api("/api/job");
    if (status.number !== started.number) {  // 곧바로 다음 작업이 시작됐다: 끝난 작업의 결과가 내 것이면 그것을 쓴다.
      if (status.last?.number !== started.number) throw new Error("다른 작업이 시작돼 이 작업의 결과를 받지 못했어요. 목록을 다시 확인하세요.");
      status = status.last;
    }
  }
  box.classList.add("hidden");
  if (status.error) throw new Error(status.error);
  return status.result;
}

// ---------- 화면 색·창 ----------
const applyTheme = followTheme(() => state?.settings.theme);

$("theme").addEventListener("click", async () => {
  if (!state) return;
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  const windows = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  state.settings.theme = next === windows ? "auto" : next;  // Windows와 같은 쪽이면 auto: 다시 Windows 색을 따른다.
  applyTheme();
  await api("/api/settings", {theme: state.settings.theme}).catch(() => {});
  windowCall("lock_reload");  // 잠금 창이 열려 있으면 같은 색으로
});

const windowCall = (action, data = {}) => api("/api/window", {action, ...data}).catch(() => null);  // 창 밖(검사용 브라우저)에서는 아무 일도 없다

$("w-min").addEventListener("click", () => windowCall("minimize"));
$("w-close").addEventListener("click", async () => {
  const job = await api("/api/job").catch(() => null);
  // changes: 파일을 바꾸는 작업만 끝까지 한다(읽기·받기만 하는 jobs.CUTTABLE은 닫으면 끊겨 묻지 않는다).
  if (job?.running && job.changes && !(await ask({title: "작업이 아직 돌고 있어요. 창을 닫을까요?", text: `창은 바로 닫히고, ‘${job.name}’ 작업은 끝까지 마친 뒤 프로그램이 꺼져요.`}))) return;
  windowCall("close");
});

// 새 버전 알림: 위쪽 막대 → [업데이트]로 릴리스 노트 창 → [지금 업데이트]면 서버가 지문을 확인하고 조용한 설치를 띄운 뒤 창을 닫는다.
let newVersion = null;
const noteText = (notes) => notes.replace(/^#{2,3} +/gm, "■ ");  // 마크다운 머리만 글자로 바꾼다. 나머지는 그대로 글자로 보인다.
async function checkUpdate() {
  if (!state.settings.update_check) return;
  newVersion = (await api("/api/update").catch(() => null))?.update || null;
  if (!newVersion) return;
  $("update-text").textContent = `새 버전 v${newVersion.version}이 있어요`;
  $("update-bar").classList.remove("hidden");
}
$("update-open").addEventListener("click", async () => {
  const {current, version, notes} = newVersion;
  const text = notes ? noteText(notes) : "릴리스 노트를 받지 못했어요. GitHub Releases에서 볼 수 있어요.";
  if (!(await ask({title: `v${current} → v${version}에서 바뀐 것`, text, ok: "지금 업데이트", cancel: "나중에", long: true}))) return;
  $("update-bar").classList.add("hidden");  // 받는 동안 다시 누르지 않게(서버도 두 번째는 거절한다)
  try {
    await api("/api/update", {version});
  } catch (e) {
    $("update-bar").classList.remove("hidden");
    return toast(e.message);
  }
  windowCall("close");  // 설치가 끝나면 다시 연다(/RELAUNCH)
});
async function toggleMaximize() {
  const answer = await windowCall("toggle");
  if (answer) document.documentElement.classList.toggle("maximized", answer.maximized);
}
$("w-max").addEventListener("click", toggleMaximize);
$("titlebar").addEventListener("dblclick", (e) => { if (!e.target.closest("button")) toggleMaximize(); });
// pywebview는 끌기 영역 안의 모든 mousedown에서 창 끌기를 시작한다. 버튼에서 시작하면 최대화 뒤 마우스가 움직일 때 창을 원래 자리로 옮겨 버린다.
$("titlebar").querySelectorAll("button").forEach((b) => b.addEventListener("mousedown", (e) => e.stopPropagation()));

// 가장자리 손잡이: 테두리 없는 창이라 크기 조절을 직접 한다. 누를 때의 창 위치·크기에서 마우스가 움직인 만큼 바꾼다.
document.querySelectorAll(".grip").forEach((grip) => grip.addEventListener("mousedown", async (e) => {
  e.preventDefault();
  const start = await windowCall("bounds");
  if (!start || start.maximized) return;
  const edge = grip.dataset.edge, x0 = e.screenX, y0 = e.screenY;
  let pending = null, sending = false;
  const send = async () => {
    if (sending || !pending) return;
    sending = true;
    const next = pending;
    pending = null;
    await windowCall("place", next);
    sending = false;
    send();
  };
  const move = (m) => {
    if (!m.buttons) return up();  // 창 밖에서 놓으면 mouseup이 오지 않는다.
    const dx = m.screenX - x0, dy = m.screenY - y0;
    const box = {x: start.x, y: start.y, width: start.width, height: start.height};
    if (edge.includes("e")) box.width = start.width + dx;
    if (edge.includes("s")) box.height = start.height + dy;
    if (edge.includes("w")) { box.width = Math.max(start.width - dx, start.min[0]); box.x = start.x + start.width - box.width; }
    if (edge.includes("n")) { box.height = Math.max(start.height - dy, start.min[1]); box.y = start.y + start.height - box.height; }
    pending = box;
    send();
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}));

// ---------- 칸 너비: 왼쪽 폴더 목록과 오른쪽 상세 사이의 경계선을 끌어 바꾸고 설정에 기억한다 ----------
// 범위와 기본값은 서버(settings.PANES → state.panes). 저장값 0은 기본. 가운데 파일 표는 MIDDLE_MIN px 넘게 남긴다.
const MIDDLE_MIN = 400;
const paneBox = (name) => (name === "pane_left" ? document.querySelector(".pane-l") : $("detail"));
const setPane = (name, px) => $("page-files").style.setProperty("--" + name, px + "px");

function fitPane(name, px) {
  const range = state.panes[name];
  const other = name === "pane_left" ? $("detail") : document.querySelector(".pane-l");
  const room = $("page-files").clientWidth - (other.classList.contains("hidden") ? 0 : other.offsetWidth) - MIDDLE_MIN;
  return Math.round(Math.max(range.min, Math.min(range.max, room > 0 ? room : range.max, px)));
}

function showPanes() {
  for (const name of Object.keys(state.panes)) setPane(name, fitPane(name, state.settings[name] || state.panes[name].default));
}
addEventListener("resize", () => state && showPanes());

async function savePane(name, px) {
  try {
    state.settings = (await api("/api/settings", {[name]: px})).settings;
  } catch (e) {
    toast(e.message);
  }
}

document.querySelectorAll(".pane-grip").forEach((grip) => {
  const name = grip.dataset.pane, sign = name === "pane_left" ? 1 : -1;  // 오른쪽 상세는 왼쪽으로 끌면 넓어진다.
  grip.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const x0 = e.clientX, start = paneBox(name).offsetWidth;
    let width = start;
    grip.classList.add("on");
    document.documentElement.classList.add("pane-dragging");
    // 놓을 때 생기는 click을 화면이 '빈 곳 클릭'(상세 닫기)으로 받지 않게 한 번 막는다.
    const swallow = (c) => c.stopPropagation();
    document.addEventListener("click", swallow, true);
    const move = (m) => {
      if (!m.buttons) return up();  // 창 밖에서 놓으면 mouseup이 오지 않는다.
      width = fitPane(name, start + sign * (m.clientX - x0));
      setPane(name, width);
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      setTimeout(() => document.removeEventListener("click", swallow, true), 0);
      grip.classList.remove("on");
      document.documentElement.classList.remove("pane-dragging");
      if (width !== start) savePane(name, width);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
  grip.addEventListener("dblclick", async () => {
    await savePane(name, 0);
    showPanes();
  });
});

// ---------- 상태 ----------
async function loadState() {
  state = await api("/api/state");
  applyTheme();
  showPanes();
  source = source || state.settings.source;
  dest = dest || state.settings.dest;
  archiveDest = archiveDest || state.settings.archive_dest;
  await drawTree();
  showIndex(state.index);
  showOrganize();
  showArchive();
  showSetup();
}

function showIndex(status) {
  const progress = status.total ? `${status.done.toLocaleString()} / ${status.total.toLocaleString()}` : `${status.done.toLocaleString()}개`;
  $("index-status").textContent = status.running
    ? `색인하는 중… ${status.step} ${progress}`
    : status.error ? "색인 실패: " + status.error
    : status.stopped ? "색인을 멈췄어요. 다시 누르면 남은 것부터 이어서 해요."
    : status.finished ? `색인 갱신 ${fullDate(status.finished)}` : "";
  $("index-progress").textContent = status.running ? status.current : "";
  $("reindex").disabled = status.running;
  document.querySelectorAll("[data-stop]").forEach((b) => b.classList.toggle("hidden", !status.running));
}

document.querySelectorAll("[data-stop]").forEach((button) => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    showIndex(await api("/api/index/stop", {}));
  } finally {
    button.disabled = false;
  }
}));

let shown = 0;  // 색인 중에 마지막으로 화면을 새로 읽은 때

async function pollIndex() {
  let again = true;  // 요청이 한 번 실패해도 멈추지 않는다(멈추면 "색인하는 중…"에 그대로 남았다)
  try {
    const status = await api("/api/index");
    showIndex(status);
    if (!status.running || Date.now() - shown > 10000) {  // 색인하는 동안에도 10초마다 숫자·폴더·목록을 새로 읽어 저장된 만큼 보여 준다.
      shown = Date.now();
      await loadState();
      await loadFiles();
    }
    again = status.running;  // 새로 읽은 뒤에만: 마지막 읽기가 실패하면 다시 한다(숫자·폴더가 옛것으로 남았다)
  } catch {
    // 다음 차례에 다시 읽는다.
  }
  if (again) setTimeout(pollIndex, 1000);
}

function showPage(name) {
  page = name;
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-" + name));
  document.querySelectorAll(".files-only").forEach((b) => b.classList.toggle("hidden", name !== "files"));
  $("more").classList.add("hidden");
  $("back").classList.toggle("hidden", name === "files");
  $("ttl").textContent = PAGES[name];
  if (name === "games") showGames();
}

document.querySelectorAll("[data-page]").forEach((b) => b.addEventListener("click", () => showPage(b.dataset.page)));
$("back").addEventListener("click", () => showPage("files"));

// ---------- 왼쪽 폴더 목록 ----------
// 맨 위는 '모든 파일', 그 아래 색인한 폴더들. 형제끼리는 용량이 큰 폴더부터. 막대는 가장 큰 맨 위 폴더에 견준 용량(제곱근).
// 줄마다 그 아래 전체의 용량과 파일 수(작게). 오른쪽 클릭은 폴더 메뉴(잠그기, 차단 폴더로).
// 이름을 누르면 그 폴더(하위 포함)의 파일이 가운데에 나오고 펼쳐진다. ▸/▾는 펼치기·접기만 한다.
// 가운데에서 파일을 고르면 그 폴더까지 펼치고, 폴더 아래에 고른 파일 한 줄을 붙인다(누르면 탐색기에서 연다).
const bySize = (list) => [...list].sort((a, b) => b.size - a.size || b.files - a.files);
const amounts = (bytes, files) => [el("span", {textContent: size(bytes)}), el("small", {textContent: files.toLocaleString() + "개"})];

function folderRow(folder, depth, biggest, top) {
  const key = lower(folder.path);
  const open = opened.has(key);
  const caret = el("span", {className: "c", textContent: folder.has_children ? (open ? "▾" : "▸") : ""});
  const name = el("div", {className: "nm"}, caret);
  name.style.paddingLeft = depth * 18 + "px";
  name.insertAdjacentHTML("beforeend", FOLDER_ICON);
  name.append(el("span", {textContent: folder.name, style: top ? "font-weight:600" : ""}));
  const bar = el("div", {className: "bar"}, el("i"));
  bar.firstChild.style.width = Math.min(100, Math.sqrt(folder.size / biggest) * 100).toFixed(1) + "%";
  const row = el("div", {className: "fl", title: folder.path}, name, el("div", {className: "m"}, bar, ...amounts(folder.size, folder.files)));
  row.dataset.path = folder.path;
  if (top) row.dataset.top = "1";
  caret.addEventListener("click", (e) => {
    e.stopPropagation();
    if (folder.has_children) toggleFolder(folder, !open);
  });
  row.addEventListener("click", () => {
    openScope({folder: folder.path, name: folder.name});
    showDetail({...folder, folder: true});
    if (folder.has_children && !open) toggleFolder(folder, true);
  });
  if (!top) {  // 색인한 맨 위 폴더는 통째로 옮기거나 지우지 않는다.
    row.draggable = true;
    row.addEventListener("dragstart", (e) => startDrag(e, [folder.path], `폴더 '${folder.name}'`));
  }
  dropTarget(row, (paths) => moveTo(paths, folder.path, folder.name));
  return row;
}

// 폴더 바로 아래 폴더들을 읽어 children에 둔다(이미 있으면 그대로). 그 폴더의 key.
async function loadChildren(path) {
  const key = lower(path);
  if (!children.has(key)) children.set(key, (await api("/api/tree?path=" + encodeURIComponent(path))).folders);
  return key;
}

// 폴더들(위에서 아래 순서)을 펼친다.
async function expandChain(folders) {
  for (const f of folders) opened.add(await loadChildren(f.path));
}

async function toggleFolder(folder, open) {
  const key = open ? await loadChildren(folder.path) : lower(folder.path);
  open ? opened.add(key) : opened.delete(key);
  await drawTree(false);
}

// 왼쪽에서 고른 드라이브(설정 drive). 연결되지 않은 드라이브면 전체로 본다.
const chosenDrive = () => (state.drives.some((d) => d.path === state.settings.drive) ? state.settings.drive : "");
const drivePlaces = () => (chosenDrive() ? state.places.filter((p) => lower(p.path).startsWith(lower(chosenDrive()))) : state.places);

async function drawTree(reload = true) {
  if (reload) children.clear();
  const drive = chosenDrive();
  $("drive").textContent = (drive || "전체 드라이브") + " ▾";
  const places = bySize(drivePlaces());
  const biggest = Math.max(1, ...places.map((p) => p.size));
  const total = places.reduce((sum, p) => sum + p.size, 0);  // 색인한 맨 위 폴더끼리는 겹치지 않는다.
  const count = drive ? places.reduce((sum, p) => sum + p.files, 0) : state.counts.all;
  const all = el("div", {className: "fl"}, el("div", {className: "nm"}, el("span", {className: "c"}), el("span", {textContent: drive ? `모든 파일 (${drive})` : "모든 파일", style: "font-weight:600"})),
    el("div", {className: "m"}, el("div", {style: "flex:1"}), ...amounts(total, count)));
  all.dataset.all = "1";
  all.addEventListener("click", () => openScope({all: true}));
  const list = [all];
  const add = async (folders, depth, top) => {
    for (const folder of bySize(folders)) {
      list.push(folderRow(folder, depth, biggest, top));
      const key = lower(folder.path);
      if (pickedFolder && key === lower(pickedFolder)) list.push(pickedRow());
      if (opened.has(key)) {
        await loadChildren(folder.path);
        await add(children.get(key), depth + 1, false);
      }
    }
  };
  await add(places, 0, true);
  if (drive && !places.length) list.push(emptyDrive(drive));
  $("tree").replaceChildren(...list);
  $("folders-sum").textContent = `용량이 큰 폴더부터 · 합계 ${size(total)}`;
  markTree();
}

// 고른 드라이브에 색인한 폴더가 없을 때: 드라이브 전체를 색인할 폴더에 넣고 색인을 시작하는 단추.
function emptyDrive(drive) {
  const button = el("button", {className: "btn s", textContent: "이 드라이브 색인에 넣기"});
  button.addEventListener("click", async () => {
    if (!(await saveSettings({roots: [...state.settings.roots, drive]}, "index-progress"))) return toast($("index-progress").textContent);
    try {
      showIndex(await api("/api/index", {}));
      pollIndex();
    } catch (e) {
      toast(e.message);
    }
  });
  return el("div", {className: "fl-empty"}, el("span", {textContent: `${drive}에서 색인한 폴더가 없어요.`}), button);
}

$("drive").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("drive-menu");
  const items = [["", "전체 드라이브"], ...state.drives.map((d) => [d.path, `${d.path} (${d.type})`])];
  menu.replaceChildren(...items.map(([value, label]) => {
    const b = el("button", {textContent: (value === chosenDrive() ? "✓ " : "") + label});
    b.addEventListener("click", async () => {
      menu.classList.add("hidden");
      if (!(await saveSettings({drive: value}, "index-progress"))) return;
      await drawTree(false);
      if (scope.all) loadFiles();
    });
    return b;
  }));
  menu.classList.remove("hidden");
  const box = e.currentTarget.getBoundingClientRect();
  Object.assign(menu.style, {left: Math.min(box.left, innerWidth - menu.offsetWidth - 4) + "px", top: box.bottom + 4 + "px"});
});
document.addEventListener("click", (e) => { if (!e.target.closest("#drive-menu")) $("drive-menu").classList.add("hidden"); });

function markTree() {
  $("tree").querySelectorAll(".fl").forEach((row) => {
    row.classList.toggle("cur", scope.all ? !!row.dataset.all : !!row.dataset.path && lower(row.dataset.path) === lower(scope.folder || ""));
    row.classList.toggle("here", !!pickedFolder && !!row.dataset.path && lower(row.dataset.path) === lower(pickedFolder));
  });
}

let pickedFolder = null;  // 고른 파일이 든 폴더(나무에 고른 파일 한 줄을 붙일 곳)

function pickedRow() {
  const name = el("div", {className: "nm"}, el("span", {className: "c", textContent: "↳"}), el("span", {textContent: picked.name}));
  name.style.paddingLeft = "36px";
  const row = el("div", {className: "fl file", title: "탐색기에서 열기: " + picked.path}, name, el("div"));
  row.addEventListener("click", () => openPath(picked.path, true));
  return row;
}

async function showFileInTree(row) {
  pickedFolder = null;
  if (row) {
    const {folders} = await api("/api/chain?path=" + encodeURIComponent(row.path));
    if (picked !== row) return;
    await expandChain(folders.slice(0, -1));  // 고른 파일이 든 폴더의 위 폴더들을 펼친다
    if (picked !== row) return;  // 펼치는 동안 다른 것을 골랐다
    pickedFolder = folders.length ? folders[folders.length - 1].path : null;
  }
  await drawTree(false);
  $("tree").querySelector(".fl.file")?.scrollIntoView({block: "nearest"});
}

// ---------- 목록 ----------
function openScope(next, view = "", remember = true) {
  if (remember && page === "files" && JSON.stringify(next) !== JSON.stringify(scope)) trail.push(scope);
  scope = next;
  filter = view;
  kind = "";
  ext = "";
  selected.clear();
  showDetail(null);
  showPage("files");
  const loading = loadFiles();
  markTree();
  return loading;
}

async function goBack() {
  if (page !== "files") return showPage("files");
  try {
    while (trail.length && await gone(trail[trail.length - 1])) trail.pop();  // 그사이 색인에서 빠진 폴더로는 돌아가지 않는다
  } catch (e) {
    return toast(e.message);
  }
  if (!trail.length) return toast("더 돌아갈 곳이 없어요");
  openScope(trail.pop(), "", false);
  toast("이전 폴더로 돌아왔어요");
}

let lastFound = null;  // 직전 목록의 답(정렬만 바꿀 때 개수·칩은 이것을 그대로 쓴다)

// rowsOnly(정확히 true): 정렬만 바뀌었을 때. 서버는 줄만 주고(개수를 다시 세지 않아 빠르다), 나머지는 직전 답을 쓴다.
// .then(loadFiles)처럼 콜백으로 넘기면 앞 단계의 답이 첫 인자로 들어오므로 true만 받는다.
// 탐색기처럼 볼 때: 폴더 하나를 보고 검색어·[조건]·모아 보기·추정이 없으면, 바로 아래 폴더 줄과 그 폴더에 바로 든 파일만.
const browsing = () => !!scope.folder && !scope.same && !scope.tag && !filter && !$("name").value.trim() && !Object.values(more).some((v) => String(v).trim());

// 폴더 줄의 정렬: 이름이면 이름, 크기면 용량, 날짜면 이름 순(폴더 날짜는 색인하지 않는다).
function sortFolders(list) {
  const by = sort === "size" ? (a, b) => a.size - b.size : (a, b) => a.name.localeCompare(b.name, "ko");
  return [...list].sort((a, b) => (order === "asc" || sort === "date" ? by(a, b) : by(b, a)));
}

async function loadFiles(rowsOnly) {
  const number = ++listed;
  const query = {...more, name: $("name").value, kind, ext, sort, order};
  const direct = browsing();
  if (direct) query.direct = true;
  if (rowsOnly === true && lastFound) query.rows_only = true;
  if (filter) query.view = filter;
  if (scope.folder) query.folder = scope.folder;
  if (scope.all && chosenDrive()) query.drive = chosenDrive();
  if (scope.same) query.same_as = scope.same;
  if (tag || scope.tag) query.tag = tag || scope.tag;
  try {
    const [found, chain, sub] = await Promise.all([api("/api/files", query),
      scope.folder ? api("/api/chain?folder=1&path=" + encodeURIComponent(scope.folder)) : Promise.resolve({folders: []}),
      direct && !kind ? api("/api/tree?path=" + encodeURIComponent(scope.folder)) : Promise.resolve({folders: []})]);
    if (number !== listed) return;
    $("list-error").classList.add("hidden");
    lastFound = query.rows_only ? {...lastFound, ...found} : found;
    showFiles(lastFound, chain.folders, sortFolders(sub.folders).map((f) => ({...f, folder: true})));
  } catch (e) {
    $("list-error").textContent = e.message;
    $("list-error").classList.remove("hidden");
  }
}

function chips(box, items, current, onPick) {
  box.replaceChildren(...items.map(([value, label, n]) => {
    const button = el("button", {className: (current === value ? "on" : "") + (n ? "" : " none")}, label, el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => onPick(value));
    return button;
  }));
}

function showFiles(found, chain, dirs = []) {
  $("title").textContent = scope.same ? `같은 파일: ${scope.name}` : scope.tag ? (scope.tag.startsWith("user:") ? scope.name : `추정: ${scope.name}`) : scope.folder ? scope.name
    : chosenDrive() ? `모든 파일 · ${chosenDrive()}` : "모든 파일";
  const where = chain.length ? chain.map((f) => f.name).join(" › ") + " · " : "";
  $("summary").textContent = `${where}${dirs.length ? `폴더 ${dirs.length.toLocaleString()}개 · ` : ""}파일 ${found.total.toLocaleString()}개 · ${size(found.bytes)}`
    + (found.more ? ` · 앞의 ${found.rows.length.toLocaleString()}개만 보여요` : "");
  const viewCounts = Object.fromEntries(found.views);
  chips($("filters"), Object.keys(VIEWS).map((v) => [v, viewTitle(v), viewCounts[v]]), filter, (v) => { filter = filter === v ? "" : v; loadFiles(); });
  // 종류는 서버가 많이 누른 순서로 준다(search.chip_order). [전체]는 늘 맨 앞.
  const all = found.kinds.reduce((sum, [, n]) => sum + n, 0);
  chips($("chips"), [["", "전체", all], ...found.kinds.map(([k, n]) => [k, k, n])], kind, (value) => {
    kind = value;
    ext = "";
    tag = "";
    (value ? countChip(value) : Promise.resolve()).then(loadFiles);
  });
  const inKind = found.exts.reduce((sum, [, n]) => sum + n, 0);
  chips($("exts"), [["", "모두", inKind], ...found.exts.map(([e, n]) => [e, e.replace(".", "") || "(확장자 없음)", n])], ext, (value) => { ext = value; loadFiles(); });
  $("exts-bar").classList.toggle("hidden", !kind || !found.exts.length);
  chips($("tags"), [["", "추정 모두", found.total], ...found.tags.map(([t, n]) => [t, tagName(t), n])], tag, (value) => { tag = tag === value ? "" : value; loadFiles(); });
  $("tags-bar").classList.toggle("hidden", !found.tags.length);
  document.querySelectorAll("#files th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === sort);
    th.textContent = th.textContent.replace(/ [↑↓]$/, "") + (th.dataset.sort === sort ? (order === "asc" ? " ↑" : " ↓") : "");
  });
  rows = [...dirs, ...found.rows];  // 폴더 줄도 줄로 다룬다(고르기·Shift 범위·옮기기·휴지통이 그대로 된다).
  const shown = new Set(rows.map((r) => lower(r.path)));
  for (const key of [...selected.keys()]) if (!shown.has(key)) selected.delete(key);  // 목록에서 빠진 줄은 고르지 않은 것으로
  const stale = `${state.settings.stale_days}일 넘게 안 열었어요`;
  const grouped = state.settings.list_group === "folder" && !scope.same && !browsing();
  let lastFolder = null;
  $("files").tBodies[0].replaceChildren(...rows.flatMap((row, i) => {
    if (row.folder) return [folderLine(row, i)];
    const folder = dirName(row.path);
    const head = grouped && folder !== lastFolder ? el("tr", {className: "group"}, el("td", {colSpan: 4, textContent: folder, title: folder})) : null;
    lastFolder = folder;
    const name = el("td", {className: "name"}, el("span", {className: "ext", textContent: (extOf(row.name).slice(1) || "?").slice(0, 5).toUpperCase()}), row.name,
      scope.same ? whyPill(row) : samePill(row),
      row.stale ? el("span", {className: "pill", textContent: stale}) : null,
      row.snippet ? el("div", {className: "snippet", textContent: "… " + row.snippet.replace(/\s+/g, " ") + " …"}) : null,
      scope.same ? el("div", {className: "path", textContent: dirName(row.path), title: row.path}) : null);
    const tr = el("tr", {}, el("td", {className: "ck"}, el("input", {type: "checkbox"})), name,
      el("td", {className: "n mute", textContent: size(row.size)}), el("td", {className: "mute", textContent: shortDate(row.date)}));
    tr.row = row;
    tr.index = i;
    return head ? [head, tr] : [tr];
  }));
  $("empty").textContent = state.counts.all ? "조건에 맞는 파일이 없어요." : "아직 색인이 없어요. 설정(톱니)에서 [색인 갱신]을 누르세요.";
  $("empty").classList.toggle("hidden", rows.length > 0);
  showSelection();
}

// 가운데 목록의 폴더 줄: 한 번 누르면 상세에 폴더 설명, 두 번 누르면 들어간다. 파일을 끌어 놓으면 그 안으로 옮긴다.
function folderLine(row, i) {
  const name = el("td", {className: "name", title: row.path});
  name.insertAdjacentHTML("beforeend", FOLDER_ICON);
  name.append(row.name, el("small", {textContent: `파일 ${row.files.toLocaleString()}개`}));
  const tr = el("tr", {className: "dir"}, el("td", {className: "ck"}, el("input", {type: "checkbox"})), name,
    el("td", {className: "n mute", textContent: size(row.size)}), el("td", {className: "mute"}));
  tr.row = row;
  tr.index = i;
  dropTarget(tr, (paths) => moveTo(paths, row.path, row.name));
  return tr;
}

// 폴더 줄을 두 번 누르면 그 폴더로 들어가고, 왼쪽 나무도 그 폴더까지 펼친다.
async function enterFolder(row) {
  openScope({folder: row.path, name: row.name});
  const {folders} = await api("/api/chain?folder=1&path=" + encodeURIComponent(row.path));
  await expandChain(folders.slice(0, -1));
  await drawTree(false);
}

// 추정 칩의 이름: 종류는 서버의 분류표(recognize.LABELS), 사람은 번호, 내 분류 모델은 설정에서 붙인 이름.
function tagName(value) {
  const cut = value.indexOf(":");  // 내 태그는 라벨 안에 ':'가 있을 수 있어 첫 ':'에서만 나눈다
  const kindName = value.slice(0, cut), label = value.slice(cut + 1);
  if (kindName === "user") return "#" + label;
  if (kindName === "person") return `사람 ${label}`;
  if (kindName === "custom") return state.settings.custom_labels[Number(label)]?.name || `분류 ${Number(label) + 1}`;
  return state.labels[kindName]?.[label] || label;
}

// 같은 파일 태그: 누르면 그 파일과 같은 파일들만(scope.same) 경로와 함께 보여 준다. 이전 목록은 빈 곳 오른쪽 클릭으로 돌아간다.
function samePill(row) {
  if (!row.duplicate) return null;
  const pill = el("button", {className: "pill link", textContent: WHY[row.duplicate], title: "누르면 이 파일과 같은 파일들을 경로와 함께 봐요"});
  pill.addEventListener("click", (e) => {
    e.stopPropagation();
    openScope({same: row.path, name: row.name});
  });
  return pill;
}

// 같은 파일 목록에서: 고른 파일은 '고른 파일', 나머지는 그 파일과 같은 까닭(사진·동영상은 유사도).
function whyPill(row) {
  const text = row.why === "self" ? "고른 파일" : WHY[row.why] + (row.score ? ` ${row.score}%` : "");
  return el("span", {className: "pill" + (row.why === "self" ? " on" : ""), textContent: text});
}

let typing = null;
$("name").addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(loadFiles, 250);
});

$("more-toggle").addEventListener("click", (e) => {
  $("more").classList.toggle("hidden");
  e.currentTarget.classList.toggle("p", !$("more").classList.contains("hidden"));
});
$("more").addEventListener("submit", (e) => {
  e.preventDefault();
  more = Object.fromEntries(new FormData(e.target));
  loadFiles();
});
$("more-clear").addEventListener("click", () => {
  $("more").reset();
  more = {};
  loadFiles();
});

document.querySelectorAll("#files th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const next = th.dataset.sort;
  order = next === sort ? (order === "asc" ? "desc" : "asc") : (next === "name" ? "asc" : "desc");
  sort = next;
  loadFiles(true);
}));

function countChip(name) {
  return api("/api/chip", {kind: name}).catch(() => {});  // 누른 횟수를 저장한다(다음 목록부터 많이 누른 칩이 왼쪽으로).
}

// 한 줄 칩 바: 휠을 굴리면 좌우로, 눌러 끌면 좌우로 민다. 끌었을 때는 손을 뗄 때의 클릭을 무시한다.
function scroller(bar) {
  bar.addEventListener("wheel", (e) => {
    if (bar.scrollWidth <= bar.clientWidth) return;
    e.preventDefault();
    bar.scrollLeft += e.deltaY || e.deltaX;
  }, {passive: false});
  let start = null, panned = false;
  bar.addEventListener("pointerdown", (e) => {
    if (e.button === 0) { start = {x: e.clientX, left: bar.scrollLeft}; panned = false; }
  });
  document.addEventListener("pointermove", (e) => {
    if (!start) return;
    if (!panned && Math.abs(e.clientX - start.x) < 5) return;
    panned = true;
    bar.classList.add("panning");
    bar.scrollLeft = start.left - (e.clientX - start.x);
  });
  document.addEventListener("pointerup", () => {
    start = null;
    bar.classList.remove("panning");
  });
  bar.addEventListener("click", (e) => {
    if (panned) { e.stopPropagation(); e.preventDefault(); panned = false; }
  }, true);
}
["filters", "chips", "exts"].forEach((id) => scroller($(id)));

// ---------- 고르기 ----------
// 클릭: 그 줄만. 체크 칸·Ctrl+클릭: 더하거나 빼기. Shift+클릭: anchor부터 그 줄까지. 고르지 않은 줄이나 표 아래 빈 곳에서 끌기: 사각형 안의 줄.
function showSelection() {
  if (selected.size && picked && !selected.has(lower(picked.path))) return showDetail([...selected.values()].at(-1));
  const current = picked && lower(picked.path);
  document.querySelectorAll("#files tbody tr:not(.group)").forEach((tr) => {
    const key = lower(tr.row.path);
    const on = selected.has(key);
    tr.classList.toggle("sel", on);
    tr.classList.toggle("cur", key === current);
    tr.querySelector("input").checked = on;
  });
  const n = selected.size;
  const bytes = [...selected.values()].reduce((sum, r) => sum + (r.size || 0), 0);
  $("check-all").checked = n > 0 && n === rows.length;
  $("sbar").replaceChildren(...(n
    ? [el("b", {textContent: `${n.toLocaleString()}개 선택됨 · ${size(bytes)}`}), el("span", {textContent: "위쪽 휴지통이나 왼쪽 폴더에 끌어다 놓을 수 있어요"})]
    : [el("span", {textContent: "파일을 누르면 상세 정보가 열리고, 빈 곳을 누르면 닫혀요. 빈 곳을 오른쪽 클릭하면 이전 폴더로 돌아가요."})]));
  $("detail-multi").classList.toggle("hidden", n < 2);
  $("detail-multi").textContent = `${n.toLocaleString()}개 선택됨 · ${size(bytes)} · 옮기기와 휴지통, 태그 달기는 고른 것 모두에 해요.`;
  $("act-rename").disabled = n > 1;
}

function selectedPaths() {
  return selected.size ? [...selected.values()].map((r) => r.path) : picked ? [picked.path] : [];
}

let marqueeJustEnded = false;  // 사각형으로 고른 직후의 click은 무시한다.

$("files").addEventListener("click", (e) => {
  const tr = e.target.closest("tbody tr:not(.group)");
  if (!tr || marqueeJustEnded) return;
  e.stopPropagation();
  const key = lower(tr.row.path);
  if (e.target.type === "checkbox" || e.ctrlKey || e.metaKey) {
    selected.has(key) ? selected.delete(key) : selected.set(key, tr.row);
    anchor = tr.index;
    if (!selected.has(key)) return showDetail([...selected.values()].at(-1) ?? null);
  } else if (e.shiftKey && anchor >= 0) {
    selected.clear();
    const [from, to] = [Math.min(anchor, tr.index), Math.max(anchor, tr.index)];
    rows.slice(from, to + 1).forEach((r) => selected.set(lower(r.path), r));
  } else {
    selected.clear();
    selected.set(key, tr.row);
    anchor = tr.index;
  }
  showDetail(tr.row);
});

$("check-all").addEventListener("click", (e) => {
  e.stopPropagation();
  selected.clear();
  if (e.target.checked) rows.forEach((r) => selected.set(lower(r.path), r));
  showSelection();
});

$("files").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tbody tr:not(.group)");
  if (tr) tr.row.folder ? enterFolder(tr.row) : openPath(tr.row.path, false);
});

$("files").addEventListener("mousedown", (e) => {
  const tr = e.target.closest("tbody tr:not(.group)");
  if (e.button !== 0 || e.target.type === "checkbox") return;
  if (tr && selected.has(lower(tr.row.path)) && !e.ctrlKey && !e.shiftKey) {
    tr.draggable = true;  // 고른 줄에서 끌면 끌어 놓기
    return;
  }
  if (tr) tr.draggable = false;
  startMarquee(e);
});
$("tw").addEventListener("mousedown", (e) => {
  if (e.button === 0 && !e.target.closest("tr")) startMarquee(e);  // 표 아래 빈 곳에서도 사각형 고르기
});

function startMarquee(e) {
  const x0 = e.clientX, y0 = e.clientY, box = $("marquee");
  let moved = false;
  const before = e.ctrlKey ? new Map(selected) : new Map();  // Ctrl을 누르고 끌면 이미 고른 것에 더한다.
  const move = (m) => {
    if (!moved && Math.hypot(m.clientX - x0, m.clientY - y0) < 5) return;
    moved = true;
    m.preventDefault();
    const [left, top, right, bottom] = [Math.min(x0, m.clientX), Math.min(y0, m.clientY), Math.max(x0, m.clientX), Math.max(y0, m.clientY)];
    Object.assign(box.style, {left: left + "px", top: top + "px", width: right - left + "px", height: bottom - top + "px"});
    box.classList.remove("hidden");
    selected.clear();
    before.forEach((v, k) => selected.set(k, v));
    document.querySelectorAll("#files tbody tr:not(.group)").forEach((tr) => {
      const r = tr.getBoundingClientRect();
      if (r.bottom > top && r.top < bottom && r.right > left && r.left < right) selected.set(lower(tr.row.path), tr.row);
    });
    showSelection();
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    box.classList.add("hidden");
    if (moved) {
      marqueeJustEnded = true;
      setTimeout(() => { marqueeJustEnded = false; }, 0);
      const last = [...selected.values()].pop();
      if (last) showDetail(last);
    }
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}

// 빈 곳을 누르면 상세를 닫고 고른 것을 푼다(시안과 같다). 버튼·입력·칩·폴더·상세 안은 빼고.
$("page-files").addEventListener("click", (e) => {
  if (marqueeJustEnded || e.target.closest("tbody tr, #detail, .fl, button, input, .chips, th")) return;
  if (!picked && !selected.size) return;
  selected.clear();
  showDetail(null);
});

// ---------- 끌어 놓기 ----------
function startDrag(e, paths, label) {
  e.dataTransfer.setData(DRAG, JSON.stringify(paths));
  e.dataTransfer.effectAllowed = "move";
  // 끌기 그림은 Windows가 따로 그려 창 하나 공유에서 빠진다. 그래서 빈 그림을 주고, 창 안의 표시가 마우스를 따라가게 한다.
  e.dataTransfer.setDragImage(BLANK, 0, 0);
  const badge = $("drag-badge");
  badge.textContent = paths.length > 1 ? `${paths.length.toLocaleString()}개 항목 이동 중` : `${label} 이동 중`;
  moveBadge(e);
}

const BLANK = Object.assign(new Image(), {src: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"});
function moveBadge(e) {
  if (!e.clientX && !e.clientY) return;  // 끌기가 끝날 때 좌표가 0,0으로 온다.
  Object.assign($("drag-badge").style, {left: e.clientX + 14 + "px", top: e.clientY + 14 + "px"});
  $("drag-badge").classList.add("on-drag");
}
document.addEventListener("dragover", moveBadge);
document.addEventListener("dragend", () => $("drag-badge").classList.remove("on-drag"));
document.addEventListener("drop", () => $("drag-badge").classList.remove("on-drag"));

$("files").addEventListener("dragstart", (e) => {
  const tr = e.target.closest("tbody tr:not(.group)");
  if (!tr) return;
  const paths = selected.has(lower(tr.row.path)) ? selectedPaths() : [tr.row.path];
  startDrag(e, paths, `파일 '${tr.row.name}'`);
});

function dropTarget(target, onDrop) {
  target.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes(DRAG)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    target.classList.add("drop");
  });
  target.addEventListener("dragleave", () => target.classList.remove("drop"));
  target.addEventListener("drop", (e) => {
    target.classList.remove("drop");
    if (!e.dataTransfer.types.includes(DRAG)) return;
    e.preventDefault();
    e.stopPropagation();
    onDrop(JSON.parse(e.dataTransfer.getData(DRAG)));
  });
}

// 범위 하나(view)를 바뀐 경로에 맞춘다: 폴더가 그 안이면 새 경로로, 같은 파일 보기의 기준이면 새 경로로. after가 null이면 지운 것(모든 파일로).
function follow(view, before, after) {
  const field = view.same ? "same" : "folder", now = view[field] && lower(view[field]), old = lower(before);
  if (!now || (now !== old && !now.startsWith(old + "\\"))) return view;
  if (!after) return {all: true};
  const moved = after + view[field].slice(before.length);
  return field === "same" ? {...view, same: moved} : {...view, folder: moved, name: baseName(moved)};
}

// 지금 범위와 돌아가기 기록(trail)을 바뀐 경로에 맞춘다.
function followScope(before, after) {
  scope = follow(scope, before, after);
  trail = trail.map((view) => follow(view, before, after));
}

const followPairs = (result) => (result?.pairs || []).forEach(([before, after]) => followScope(before, after));

// 범위(view)의 폴더나 같은 파일 보기의 기준이 색인에서 빠졌는지(잠금·비우기·차단·되돌리기 등).
async function gone(view) {
  if (view.same) return !(await api("/api/same?path=" + encodeURIComponent(view.same)).then(() => true, () => false));
  return !!view.folder && !(await api("/api/chain?folder=1&path=" + encodeURIComponent(view.folder))).folders.length;
}

// 바꾼 뒤 지금 연 폴더가 색인에 없으면 모든 파일로 간다.
async function leaveGoneFolder() {
  // 묻는 동안 다른 작업이 시작됐거나(정리 직후 누른 되돌리기 등) 범위가 바뀌었으면 늦은 답은 버린다: 그 작업이 옮긴 것을 '없음'으로 보고
  // 범위를 버리게 된다. 그 작업이 끝나면 자기 afterChange에서 범위를 따라간 뒤 다시 판단한다.
  const view = scope, started = jobsStarted;
  if (!$("job").classList.contains("hidden") || !(await gone(view)) || scope !== view || jobsStarted !== started) return;
  scope = {all: true};
  markTree();
  await loadFiles();
}

async function afterChange(message) {
  selected.clear();
  await showDetail(null);
  await loadState();
  await loadFiles();
  await leaveGoneFolder();
  if (message) toast(message);
}

async function moveTo(paths, folder, name) {
  try {
    const result = await runJob((await api("/api/move", {paths, folder})).job);
    followPairs(result);
    if (result) await afterChange(`${result.moved.toLocaleString()}개를 '${name}'${josa(name, "으로", "로")} 옮겼어요.${failedText(result)}`);
  } catch (e) {
    toast(e.message);
  }
}

async function trashPaths(paths, isFolder = false) {
  if (!paths.length) return;
  const first = baseName(paths[0]);
  const inList = rows.find((r) => lower(r.path) === lower(paths[0]));
  const folder = isFolder || !inList || !!inList.folder;  // 목록의 폴더 줄, 왼쪽에서 끌어 온 폴더, 폴더 메뉴의 [폴더 지우기]
  const more = paths.length > 1 ? ` 외 ${(paths.length - 1).toLocaleString()}개 항목` : "";
  const text = `선택한 ${folder ? "폴더" : "파일"} '${first}'${more}${more ? "을" : josa(first, "을", "를")} 휴지통으로 이동하시겠습니까?`;
  if (!(await ask({title: "휴지통으로 보낼까요?", text: text + "\n휴지통에서 다시 꺼낼 수 있어요."}))) return;
  try {
    const result = await runJob((await api("/api/trash", {paths, confirm: true})).job);
    const kept = new Set(result.failed.map((f) => lower(f.from)));
    paths.filter((p) => !kept.has(lower(p))).forEach((p) => followScope(p, null));  // 보내지 못한 폴더에서는 나가지 않는다
    await afterChange(`${result.trashed.toLocaleString()}개를 휴지통으로 보냈어요.${failedText(result)}`);
  } catch (e) {
    toast(e.message);
  }
}

dropTarget($("trash"), trashPaths);
$("trash").addEventListener("click", () => {
  if (selected.size) trashPaths(selectedPaths());
  else toast("파일을 고른 뒤 누르거나, 파일·폴더를 여기에 끌어다 놓으세요");
});

// ---------- 상세 ----------
let detailTicket = 0;  // 상세를 열 때마다 하나씩: 늦게 온 답이 그 상세의 것인지

async function showDetail(row) {
  const ticket = ++detailTicket;
  picked = row;
  $("detail").classList.toggle("hidden", !row);
  showPanes();  // 상세가 열려 가운데가 좁아지면 저장한 너비보다 줄여 맞춘다(저장값은 그대로).
  say("detail-message", "");
  showSelection();
  document.querySelectorAll("#detail .file-only").forEach((s) => s.classList.toggle("hidden", !!row?.folder));
  document.querySelectorAll("#detail .folder-only").forEach((s) => s.classList.toggle("hidden", !row?.folder));
  if (!state.settings.show_same_name) document.querySelector('[data-fold="same-name"]').classList.add("hidden");  // file-only가 켠 뒤에
  showFileInTree(row?.folder ? null : row);
  if (row?.folder) return showFolderDetail(row);
  if (!row) return;
  $("detail-name").textContent = row.name;
  $("detail-pills").replaceChildren(...[samePill(row), row.stale ? el("span", {className: "pill", textContent: `${state.settings.stale_days}일 넘게 안 열었어요`}) : null]
    .filter(Boolean));
  $("detail-preview").textContent = "";
  for (const id of ["detail-rows", "same-content", "same-name"]) $(id).replaceChildren();
  $("same-content-title").textContent = "내용이 같은 파일";
  $("same-name-title").textContent = "이름이 같은 파일";
  const q = encodeURIComponent(row.path);
  let chain, same, preview;
  try {
    [chain, same, preview] = await Promise.all([api("/api/chain?path=" + q), api("/api/same?path=" + q), api("/api/preview?path=" + q)]);
  } catch (e) {
    if (ticket === detailTicket) say("detail-message", e.message, "error");
    return;
  }
  if (ticket !== detailTicket) return;
  $("detail-preview").textContent = preview.text || (preview.readable ? "아직 내용을 읽지 않았거나 글자가 없어요." : "이 형식은 내용을 읽지 않아요(사진·동영상·hwp 등).");
  showFacts([["종류", row.kind], ["크기", size(row.size)], ["위치", crumbs(chain.folders)], ["마지막 수정", fullDate(row.modified)]]);
  showSame("same-content", "내용이 같은 파일", same.content, "내용이 같은 파일이 없어요.");
  showTags(row, ticket);
  showGuess(row, ticket);
  showProvenance(row, ticket);
  showSame("same-name", "이름이 같은 파일", same.name, "이름이 같은 파일이 없어요.");
}

// 상세 칸의 위치: 폴더마다 누르면 그 폴더를 여는 버튼(› 로 잇는다).
function crumbs(folders) {
  const where = el("div");
  folders.forEach((f, i) => {
    const button = el("button", {className: "crumb", textContent: f.name, title: f.path});
    button.addEventListener("click", () => openScope({folder: f.path, name: f.name}));
    where.append(...(i ? [" › ", button] : [button]));
  });
  return where;
}

// 상세 칸의 [이름, 값] 줄들(값은 글자이거나 요소).
function showFacts(facts) {
  $("detail-rows").replaceChildren(...facts.flatMap(([k, v]) => [el("span", {textContent: k}), typeof v === "string" ? el("div", {textContent: v}) : v]));
}

// 폴더 설명: 자동 요약(색인에서 센 것)과 내 메모.
async function showFolderDetail(row) {
  $("detail-name").textContent = row.name;
  $("detail-pills").replaceChildren();
  $("detail-rows").replaceChildren();
  $("folder-note").value = "";
  say("folder-note-message", "");
  $("folder-kinds").replaceChildren();
  $("folder-big").replaceChildren();
  const q = encodeURIComponent(row.path);
  let s, note, chain;
  try {
    [{summary: s, note}, chain] = await Promise.all([api("/api/folder_summary?path=" + q), api("/api/chain?folder=1&path=" + q)]);
  } catch (e) {
    if (picked === row) say("detail-message", e.message, "error");
    return;
  }
  if (picked !== row || !s) return;
  const same = el("button", {className: "crumb", textContent: s.same ? `${s.same.toLocaleString()}개` : "없음", disabled: !s.same});
  same.addEventListener("click", () => openScope({folder: row.path, name: row.name}, "duplicates"));
  const facts = [["종류", "폴더"], ["크기", size(s.size)], ["파일", `${s.files.toLocaleString()}개`],
    ["하위 폴더", `바로 아래 ${s.children.toLocaleString()}개 · 전체 ${s.below.toLocaleString()}개`], ["위치", crumbs(chain.folders.slice(0, -1))],
    ["마지막 수정", s.latest ? fullDate(s.latest) : "-"], ["같은 파일 의심", same]];
  showFacts(facts);
  $("folder-kinds").replaceChildren(...(s.kinds.length ? s.kinds.map(([k, n]) => el("span", {className: "pill", textContent: `${k} ${n.toLocaleString()}`}))
    : [el("span", {className: "hint", textContent: "파일이 없어요."})]));
  $("folder-big").replaceChildren(...s.biggest.map((f) => {
    const b = el("button", {className: "li", title: f.path}, el("span", {textContent: f.name}), el("span", {textContent: size(f.size)}));
    b.addEventListener("click", () => goToFile({...f, place: ""}));
    return b;
  }));
  $("folder-note").value = note;
}

$("folder-note-save").addEventListener("click", async () => {
  if (!picked?.folder) return;
  try {
    const {note} = await api("/api/note", {path: picked.path, text: $("folder-note").value});
    say("folder-note-message", note ? "저장했어요." : "메모를 지웠어요.", "ok");
  } catch (e) {
    say("folder-note-message", e.message, "error");
  }
});

// 폴더 비우기: 안의 파일·폴더를 모두 휴지통으로(폴더는 남는다). 먼저 개수를 받아 확인을 받는다.
async function emptyFolder(path) {
  try {
    const plan = await api("/api/folder/empty", {path});
    if (!plan.count) return toast("휴지통으로 보낼 것이 없어요." + (plan.refused.length ? ` 보낼 수 없는 것 ${plan.refused.length}개` : ""));
    const notes = [plan.hidden ? `숨김·시스템 파일 ${plan.hidden}개는 그대로 둬요.` : "", plan.refused.length ? `차단·잠금·구조 보호 때문에 ${plan.refused.length}개는 빼요.` : ""].filter(Boolean).join("\n");
    if (!(await ask({title: "폴더를 비울까요?", text: `${path}\n\n안의 파일·폴더 ${plan.count.toLocaleString()}개를 휴지통으로 보내요. 폴더는 남아요.\n휴지통에서 다시 꺼낼 수 있어요.${notes ? "\n" + notes : ""}`}))) return;
    const result = await runJob((await api("/api/folder/empty", {path, confirm: true})).job);
    await afterChange(`${result.trashed.toLocaleString()}개를 휴지통으로 보냈어요.${failedText(result)}`);
  } catch (e) {
    toast(e.message);
  }
}

// 같은 파일 칸은 접어 두고, 펼치거나 접은 상태는 다른 파일을 골라도 그대로 둔다(켜 둔 동안만).
document.querySelectorAll(".fold").forEach((box) => box.querySelector(".fold-head").addEventListener("click", () => box.classList.toggle("open")));

// 상세의 받은 곳·편집한 사람(그 파일에서 바로 읽은 것). 있는 것만 줄로 더한다.
async function showProvenance(row, ticket) {
  const info = await api("/api/provenance?path=" + encodeURIComponent(row.path)).catch(() => null);
  if (picked !== row || ticket !== detailTicket || !info) return;
  const lines = [["받은 곳", info.source], ["링크가 있던 곳", info.referrer], ["만든 사람", info.author], ["마지막 저장", info.last_saved_by], ["소유자", info.owner]];
  for (const [label, value] of lines.filter(([, v]) => v)) {
    $("detail-rows").append(el("span", {textContent: label}), el("div", {className: "mono", textContent: value, title: value}));
  }
}

// 상세의 내 태그(#코딩): 칩을 누르면 그 태그의 파일 검색, ×로 지우기, 입력 칸 Enter로 달기. 여러 파일을 골랐으면 고른 것 모두에 달고 지운다.
async function showTags(row, ticket) {
  const {tags} = await api("/api/tags?path=" + encodeURIComponent(row.path)).catch(() => ({tags: []}));
  if (picked !== row || ticket !== detailTicket) return;
  const box = el("div");
  const draw = (list) => {
    const n = selectedPaths().length;
    const input = el("input", {type: "text", maxLength: 50, placeholder: n > 1 ? `고른 ${n.toLocaleString()}개 모두에 #태그 추가` : "#태그 추가"});
    const send = async (path, body) => {
      try {
        draw((await api(path, {paths: selectedPaths(), ...body})).tags);
      } catch (e) {
        say("detail-message", e.message, "error");
      }
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter" && !e.isComposing && input.value.trim()) send("/api/tags", {label: input.value});
    });
    box.replaceChildren(...list.map((label) => {
      const chip = el("button", {className: "pill link", textContent: "#" + label});
      chip.addEventListener("click", () => openScope({all: true, tag: "user:" + label, name: "#" + label}));
      const x = el("button", {className: "pill-x", textContent: "×", title: "이 태그 지우기"});
      x.addEventListener("click", () => send("/api/tags/remove", {label}));
      return el("span", {className: "tag-item"}, chip, x);
    }), input);
  };
  draw(tags);
  $("detail-rows").append(el("span", {textContent: "태그"}), box);
}

// 상세의 추정(사진·동영상 인식 결과): 칩을 누르면 그 추정의 파일들을 모두 본다.
async function showGuess(row, ticket) {
  const {tags, people} = await api("/api/recognized?path=" + encodeURIComponent(row.path)).catch(() => ({tags: [], people: []}));
  if (picked !== row || ticket !== detailTicket || (!tags.length && !people.length)) return;
  const chipFor = (value, text) => {
    const b = el("button", {className: "pill link", textContent: text});
    b.addEventListener("click", () => openScope({all: true, tag: value, name: tagName(value)}));
    return b;
  };
  const box = el("div", {}, ...tags.map(([t, score]) => chipFor(t, `${tagName(t)} ${score}%`)), ...people.map((p) => chipFor(`person:${p}`, `사람 ${p}`)));
  $("detail-rows").append(el("span", {textContent: "추정"}), box);
}

function showSame(id, title, list, none) {
  $(id + "-title").textContent = list.length ? `${title} ${list.length.toLocaleString()}개${list.length >= 50 ? " 이상" : ""}` : `${title} 없음`;
  $(id).replaceChildren(...(list.length ? list.map((f) => {
    const folder = dirName(f.path);
    const where = baseName(folder) === f.place ? f.place : `${f.place} › … › ${baseName(folder)}`;
    const why = f.why ? ` · ${WHY[f.why]}${f.score ? " " + f.score + "%" : ""}` : "";
    const button = el("button", {className: "li", title: f.path}, el("span", {textContent: where + why}), el("span", {textContent: size(f.size)}));
    button.addEventListener("click", () => goToFile(f));
    return button;
  }) : [el("div", {className: "li"}, el("span", {textContent: none}))]));
}

async function goToFile(file) {
  // 같은 파일 목록에서 누른 파일로: 그 폴더를 열고 그 파일을 고른다.
  const folder = dirName(file.path);
  await openScope({folder, name: baseName(folder)});
  const row = rows.find((r) => lower(r.path) === lower(file.path));
  if (row) {
    selected.set(lower(row.path), row);
    showDetail(row);
  }
}

async function changed(row, path) {
  // 옮기거나 이름을 바꾼 뒤: 상세는 새 경로로, 목록과 숫자는 다시 읽는다.
  const name = baseName(path);
  followScope(row.path, path);
  picked = {...row, path, name, ext: extOf(name)};
  selected.clear();
  selected.set(lower(path), picked);
  await loadState();
  await loadFiles();
  await showDetail(picked);
}

$("act-move").addEventListener("click", (e) => busy(e.currentTarget, "detail-message", async () => {
  const row = picked, paths = selectedPaths();  // 옮기는 동안 다른 줄을 누르거나 빈 곳을 눌러도 옮긴 그 파일을 따라간다
  const folder = await pickPath({title: "옮길 곳 고르기"});
  if (!folder) return;
  const result = await runJob((await api("/api/move", {paths, folder})).job);
  if (!result) return;
  if (paths.length !== 1) result.pairs.forEach(([before, after]) => followScope(before, after));  // 하나면 changed가 따라간다
  if (paths.length === 1 && result.paths.length === 1) {
    await changed(row, result.paths[0]);
    say("detail-message", "옮겼어요." + failedText(result), result.failed.length ? "error" : "ok");
  } else {
    await afterChange(`${result.moved.toLocaleString()}개를 옮겼어요.${failedText(result)}`);
  }
}));

$("act-rename").addEventListener("click", async (e) => {
  const row = picked;
  const name = await ask({title: "이름 바꾸기", text: row.name, value: row.name});
  if (name === null || name === row.name) return;
  busy(e.target, "detail-message", async () => {
    const {path, note} = await api("/api/rename", {path: row.path, name});
    await changed(row, path);
    say("detail-message", note ? `이름을 바꿨어요. ${note}` : "이름을 바꿨어요.", note ? "error" : "ok");
  });
});

$("act-trash").addEventListener("click", () => trashPaths(selectedPaths()));

document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });  // 대화상자·고르기 창은 common.js가 닫는다.

// Ctrl+Z: 최근의 옮기기·이름 바꾸기·다운로드 정리를 하나씩 되돌린다(서버 undo, 최근 20개). 입력 칸·대화상자·고르기 창에서는 원래대로.
// e.code를 보므로 한글 입력 상태에서도 된다.
document.addEventListener("keydown", async (e) => {
  if (e.code !== "KeyZ" || !e.ctrlKey || e.shiftKey || e.altKey || e.repeat) return;
  if (e.target.closest?.("input, textarea") || !$("dialog").classList.contains("hidden") || !$("picker").classList.contains("hidden")) return;
  e.preventDefault();
  try {
    const {job, label} = await api("/api/undo", {});
    const result = await runJob(job);
    followPairs(result);
    await afterChange(`되돌렸어요: ${label}${result.moved ? "" : " (되돌린 것 없음)"}${failedText(result)}`);
  } catch (err) {
    toast(err.message);
  }
});

// ---------- 오른쪽 클릭: 파일 줄 위는 메뉴, 그 밖은 이전 폴더로(설정 등에서는 목록으로) ----------
document.addEventListener("contextmenu", (e) => {
  if (e.target.closest("input")) return;  // 입력 칸은 붙여넣기 메뉴 그대로
  e.preventDefault();
  if (!$("dialog").classList.contains("hidden") || !$("picker").classList.contains("hidden")) return;
  const tr = page === "files" && e.target.closest("#files tbody tr:not(.group)");
  if (tr) return showMenu(e, tr.row.path, tr.row.folder ? "folder" : "file");
  const folder = page === "files" && e.target.closest(".fl[data-path]");
  if (folder && !folder.dataset.top) return showMenu(e, folder.dataset.path, "folder");
  hideMenu();
  goBack();
});

function showMenu(e, path, kind) {
  menuPath = path;
  const menu = $("menu");
  menu.querySelectorAll("[data-for]").forEach((b) => b.classList.toggle("hidden", b.dataset.for !== kind || (!!b.dataset.admin && !state.admin)));
  menu.classList.remove("hidden");
  menu.style.left = Math.min(e.clientX, innerWidth - menu.offsetWidth - 4) + "px";
  menu.style.top = Math.min(e.clientY, innerHeight - menu.offsetHeight - 4) + "px";
}

const hideMenu = () => $("menu").classList.add("hidden");
document.addEventListener("click", (e) => { if (!e.target.closest("#menu")) hideMenu(); });
document.addEventListener("scroll", hideMenu, true);

async function openPath(path, reveal) {
  try {
    const {note} = await api("/api/open", {path, reveal});
    if (note) toast(note);
  } catch (e) {
    toast(e.message);
  }
}

$("menu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  hideMenu();
  if (act === "open") openPath(menuPath, false);
  if (act === "reveal") openPath(menuPath, true);
  if (act === "exclude") changeExclude(menuPath, false);
  if (act === "lock") openLockWindow(menuPath);
  if (act === "remove") trashPaths([menuPath], true);
  if (act === "empty") emptyFolder(menuPath);
  if (act === "block") changeBlacklist(menuPath, false);
  if (act === "protect") changeProtect(menuPath, false);
});

// ---------- 잠금 폴더: 화면 캡처·공유에서 빠지는 잠금 창(lock.html)에서만 다룬다 ----------
function openLockWindow(path = "") {
  api("/api/window", {action: "lock", path}).catch((e) => toast(e.message));
}
$("lock-open").addEventListener("click", () => openLockWindow());

// 잠금 창이 잠그거나 풀거나 꺼낸 뒤 desktop.py가 부른다(목록·숫자가 바뀐다).
async function refreshAfterLock() {
  await showDetail(null);  // 잠근 폴더를 상세에 둔 채 다루지 않게
  await loadState();
  await loadFiles();
  await leaveGoneFolder();
}

// ---------- 차단 폴더(관리자 권한일 때만 바꾼다) ----------
async function changeBlacklist(path, remove) {
  if (!remove && !await ask({title: "차단 폴더로 지정할까요?", text: `${path}\n\n이 폴더와 그 아래 전부를 색인하지 않고 어디서도 다루지 않아요. 지금까지의 색인 기록은 지워요(실제 파일은 그대로).`})) return;
  try {
    await api("/api/blacklist", {path, remove});
    await afterChange(remove ? "차단을 풀었어요. 다음 [색인 갱신]부터 다시 들어와요." : "차단 폴더로 지정했어요.");  // 연 폴더가 빠졌으면 모든 파일로
  } catch (e) {
    toast(e.message);
  }
}

async function changeProtect(path, remove) {
  if (!remove && !await ask({title: "구조 보호 폴더로 지정할까요?", text: `${path}\n\n이 폴더를 옮기거나 이름을 바꾸거나 지울 수 없고, 안의 것도 이름 바꾸기·휴지통·보관을 하지 않아요. 목록과 검색에는 그대로 나와요.`})) return;
  try {
    await api("/api/protect", {path, remove});
    await loadState();
    toast(remove ? "구조 보호를 풀었어요." : "구조 보호 폴더로 지정했어요.");
  } catch (e) {
    toast(e.message);
  }
}

$("protect-pick").addEventListener("click", async () => {
  const path = await pickPath({title: "구조 보호 폴더 고르기"});
  if (path) await changeProtect(path, false);
});

$("block-pick").addEventListener("click", async () => {
  const path = await pickPath({title: "차단 폴더 고르기"});
  if (path) await changeBlacklist(path, false);
});

$("block-find").addEventListener("click", async () => {
  const {folders} = await api("/api/blacklist/candidates");
  $("block-choices").replaceChildren(...(folders.length ? folders.map((path) => {
    const button = el("button", {className: "chip", textContent: "+ " + path});
    button.addEventListener("click", () => changeBlacklist(path, false));
    return button;
  }) : [el("span", {className: "hint", textContent: "찾은 게임 폴더가 없어요."})]));
});

async function changeExclude(path, remove) {
  try {
    await api("/api/exclude", {path, remove});
    await loadState();
    if (page === "files") await loadFiles();
    toast(remove ? "예외에서 뺐어요" : "예외로 지정했어요. 중복 의심 등 네 목록에서 빠져요.");
  } catch (e) {
    toast(e.message);
  }
}

// ---------- 다운로드 정리하기 ----------
function choice(label, path, current, onPick) {
  const button = el("button", {className: "chip" + (lower(path) === lower(current || "") ? " on" : ""), textContent: label, title: path});
  button.addEventListener("click", () => onPick(path));
  return button;
}

function showOrganize() {
  const places = [["다운로드", state.known["다운로드"]], ...state.drives.map((d) => [`${d.path} (${d.type})`, d.path])];
  $("source-choices").replaceChildren(...places.map(([label, path]) => choice(label, path, source, setSource)));
  $("source").textContent = source || "고르지 않음";
  $("dest").textContent = dest || "고르지 않음";
  $("undo").disabled = !state.undo;
  $("undo").textContent = state.undo ? `마지막 정리 되돌리기 (${state.undo}개)` : "마지막 정리 되돌리기";
}

function clearPreview() {
  $("run").disabled = true;
  $("preview-card").classList.add("hidden");
}

function setSource(path) {
  source = path;
  clearPreview();
  showOrganize();
}

$("preview").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  clearPreview();
  const found = await api("/api/organize/preview", {source, dest});
  const {moves} = found;
  ({source, dest} = found);  // 저장된 실제 경로(정션이면 가리키는 곳): to를 같은 앞부분으로 자른다
  showOrganize();
  $("preview-table").tBodies[0].replaceChildren(...moves.map((m) => el("tr", {},
    el("td", {}, el("span", {className: "ext", textContent: (extOf(baseName(m.from)).slice(1) || "?").slice(0, 5).toUpperCase()}), baseName(m.from)),
    el("td", {className: "mute", textContent: m.kind}),
    el("td", {className: "mute", textContent: m.to.slice(dest.length).replace(/^\\/, ""), title: m.to}))));
  $("preview-card").classList.toggle("hidden", !moves.length);
  const counts = {};
  moves.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
  const summary = Object.entries(counts).map(([k, n]) => `${k} ${n}`).join(", ");
  say("organize-message", moves.length ? `${moves.length}개를 옮겨요 (${summary}). 확인한 뒤 [정리]를 누르세요.` : "옮길 파일이 없어요.");
  $("run").disabled = !moves.length;
  await loadState();
}));

function report(result, verb) {
  say("organize-message", `${result.moved}개를 ${verb}.${failedText(result)}`, result.failed.length ? "error" : "ok");
}

$("run").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  const result = await runJob(await api("/api/organize/run", {source, dest}));
  followPairs(result);
  clearPreview();
  report(result, "옮겼어요");
  await afterChange();
}).then(() => { e.target.disabled = true; }));  // 옮긴 뒤에는 다시 미리보기를 해야 정리할 수 있다.

$("undo").addEventListener("click", async (e) => {
  if (!(await ask({title: "마지막 정리를 되돌릴까요?", text: `${state.undo}개를 원래 자리로 옮겨요.`}))) return;
  busy(e.target, "organize-message", async () => {
    const result = await runJob(await api("/api/organize/undo", {}));
    followPairs(result);
    clearPreview();
    report(result, "되돌렸어요");
    await afterChange();
  }).then(() => { e.target.disabled = !state.undo; });  // busy가 다시 켠 것을 남은 기록에 맞춘다
});

// ---------- 오래된 파일 보관 ----------
// ---------- 게임 ----------
// 색인이 판별한 게임 폴더 + 런처가 아는 게임. 이름을 누르면 그 폴더를 탐색기로 연다. 크기를 모르는 것은 [모르는 크기 세기]로.
async function showGames() {
  try {
    const {games} = await api("/api/games");
    $("games-table").tBodies[0].replaceChildren(...games.map((g) => {
      const name = el("button", {className: "link", textContent: g.name, title: "탐색기에서 열기"});
      name.addEventListener("click", () => api("/api/games/open", {path: g.path}).catch((e) => toast(e.message)));
      return el("tr", {}, el("td", {}, name), el("td", {className: "mute", textContent: g.launcher || "폴더로 찾음"}),
        el("td", {className: "n mute", textContent: g.size == null ? "모름" : size(g.size)}),
        el("td", {className: "mute", textContent: g.played ? new Date(g.played * 1000).toLocaleDateString() : ""}),
        el("td", {className: "mono mute", textContent: g.path, title: g.path}));
    }));
    const known = games.filter((g) => g.size != null);
    say("games-message", games.length ? "" : "찾은 게임이 없어요. 색인을 갱신하면 색인할 폴더 안의 게임 폴더를 찾아요.");
    $("games-sum").textContent = games.length ? `${games.length}개 · 아는 크기 합계 ${size(known.reduce((sum, g) => sum + g.size, 0))}` : "";
    $("games-measure").disabled = known.length === games.length;
  } catch (e) {
    say("games-message", e.message, "error");
  }
}

$("games-measure").addEventListener("click", (e) => busy(e.target, "games-message", async () => {
  await runJob((await api("/api/games/measure", {})).job);
  await showGames();
}));

$("captures-preview").addEventListener("click", (e) => busy(e.target, "captures-message", async () => {
  $("captures-run").disabled = true;
  const found = await api("/api/captures/preview", {});
  $("captures-table").tBodies[0].replaceChildren(...found.groups.map((g) => el("tr", {}, el("td", {textContent: g.game}),
    el("td", {className: "mute", textContent: g.source}), el("td", {className: "n mute", textContent: g.count.toLocaleString()}),
    el("td", {className: "mono mute", textContent: g.to, title: g.to}))));
  $("captures-card").classList.toggle("hidden", !found.count);
  $("captures-run").disabled = !found.count;
  say("captures-message", found.count ? `${found.count.toLocaleString()}개를 게임별 폴더로 옮겨요.` : "옮길 게임 캡처가 없어요.");
}));

$("captures-run").addEventListener("click", (e) => busy(e.target, "captures-message", async () => {
  const result = await runJob((await api("/api/captures/run", {})).job);
  followPairs(result);
  $("captures-card").classList.add("hidden");
  await afterChange(`게임 캡처 ${result.moved.toLocaleString()}개를 옮겼어요. Ctrl+Z로 되돌릴 수 있어요.${failedText(result)}`);
  say("captures-message", "");
}).then(() => { e.target.disabled = true; }));  // 다시 미리보기를 해야 옮길 수 있다

function showArchive() {
  $("archive-dest").textContent = archiveDest || "고르지 않음";
  if (!$("cold_days").value) $("cold_days").value = state.settings.cold_days;
}

function clearArchive() {
  $("archive-run").disabled = true;
  $("archive-card").classList.add("hidden");
}

const archiveQuery = () => ({dest: archiveDest, days: Number($("cold_days").value)});
$("cold_days").addEventListener("input", clearArchive);  // 기간을 바꾸면 미리보기와 다른 파일을 보관하게 된다

$("archive-preview").addEventListener("click", (e) => busy(e.target, "archive-message", async () => {
  clearArchive();
  const found = await api("/api/archive/preview", archiveQuery());
  $("archive-table").tBodies[0].replaceChildren(...found.kinds.map(([k, n, b]) => el("tr", {},
    el("td", {textContent: k}), el("td", {className: "n mute", textContent: n.toLocaleString()}), el("td", {className: "n mute", textContent: size(b)}))));
  $("archive-card").classList.toggle("hidden", !found.files);
  say("archive-message", found.files
    ? `${found.files.toLocaleString()}개(${size(found.bytes)})를 zip 하나로 묶고 원본은 휴지통으로 보내요.` +
      (found.blocked ? ` 휴지통으로 보낼 수 없는 곳의 ${found.blocked.toLocaleString()}개는 넣지 않아요.` : "")
    : "보관할 파일이 없어요.");
  $("archive-run").disabled = !found.files;
  state.settings = (await api("/api/state")).settings;
  archiveDest = state.settings.archive_dest;  // 저장된 실제 경로: 확인 문구와 보관 결과의 경로가 같게
  showArchive();
}));

$("archive-run").addEventListener("click", async (e) => {
  if (!(await ask({title: "보관할까요?", text: `${archiveDest}에 zip을 만들고, 확인한 뒤 원본을 휴지통으로 보내요.\n휴지통에서 다시 꺼낼 수 있어요.`}))) return;
  busy(e.target, "archive-message", async () => {
    const result = await runJob(await api("/api/archive/run", {...archiveQuery(), confirm: true}));
    clearArchive();
    say("archive-message", `${result.files.toLocaleString()}개(${size(result.original)})를 ${result.zip}(${size(result.zip_size)})로 보관했어요.\n` +
      `휴지통을 비우면 ${size(result.freed)}가 비워져요.${failedText(result)}`, result.failed.length ? "error" : "ok");
    await afterChange();
  }).then(() => { e.target.disabled = true; });  // 다시 미리보기를 해야 보관할 수 있다
});

const PICK_TITLES = {source: "정리할 곳", dest: "놓을 곳", archive: "보관할 곳", root: "색인할 폴더"};
document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
  const path = await pickPath({title: PICK_TITLES[button.dataset.pick] + " 고르기"});
  if (!path) return;
  if (button.dataset.pick === "source") setSource(path);
  else if (button.dataset.pick === "dest") { dest = path; clearPreview(); showOrganize(); }
  else if (button.dataset.pick === "archive") { archiveDest = path; clearArchive(); showArchive(); }
  else await saveSettings({roots: [...state.settings.roots, path]}, "index-progress");
}));

// ---------- 설정 ----------
// 설정 칸에서 고친 글자·숫자 칸은 저장할 때까지 표시해 둔다(다시 그릴 때 덮지 않게).
$("page-setup").addEventListener("input", (e) => {
  if (e.target.matches("input[type=text], input[type=number], #custom-labels input")) e.target.dataset.edited = "1";
});

function setField(id, value) {
  if (!$(id).dataset.edited) $(id).value = value;
}

async function saveSettings(changes, messageId) {
  try {
    state.settings = (await api("/api/settings", changes)).settings;
    for (const name of Object.keys(changes)) delete $(name)?.dataset.edited;
    if ("custom_labels" in changes) $("custom-labels").querySelectorAll("[data-edited]").forEach((input) => delete input.dataset.edited);
    showSetup();
    return true;
  } catch (e) {
    $(messageId).textContent = e.message;
    return false;
  }
}

function listRow(text, onRemove, icon = true) {
  const row = el("div");
  if (icon) row.insertAdjacentHTML("beforeend", FOLDER_ICON);
  const button = el("button", {className: "btn s", textContent: "빼기"});
  button.addEventListener("click", onRemove);
  row.append(el("span", {className: "mono", textContent: text}), button);
  return row;
}

function showSetup() {
  $("views").replaceChildren(...Object.keys(VIEWS).map((view) => {
    const card = el("button", {className: "stat" + (VIEWS[view].warn ? " w" : "")}, el("span", {textContent: viewTitle(view)}),
      el("b", {}, state.counts[view].toLocaleString(), el("i", {textContent: "개"})));
    card.addEventListener("click", () => openScope({all: true}, view));
    return card;
  }));
  $("access-off").classList.toggle("hidden", state.last_access);
  const roots = state.settings.roots;
  $("roots").replaceChildren(...roots.map((root) => listRow(root, () => saveSettings({roots: roots.filter((r) => r !== root)}, "index-progress"))));
  const has = new Set(roots.map(lower));
  const places = [...Object.entries(state.known), ...state.drives.map((d) => [d.path, d.path])].filter(([, path]) => !has.has(lower(path)));
  $("root-choices").replaceChildren(...places.map(([label, path]) => {
    const button = el("button", {className: "chip", textContent: "+ " + label, title: path});
    button.addEventListener("click", () => saveSettings({roots: [...roots, path]}, "index-progress"));
    return button;
  }));
  for (const name of ["recent_days", "stale_days", "cache_days"]) setField(name, state.settings[name]);
  drawCache();
  $("excludes").replaceChildren(...state.exclude.map((rule) =>
    listRow(`${rule.kind === "folder" ? "폴더" : "파일"}  ${rule.path}`, () => changeExclude(rule.path, true), rule.kind === "folder")));
  $("excludes-empty").classList.toggle("hidden", state.exclude.length > 0);
  $("not-admin").classList.toggle("hidden", state.admin);
  document.querySelectorAll(".admin-only").forEach((box) => box.classList.toggle("hidden", !state.admin));
  const adminRow = (path, onRemove) => {  // 관리자면 [빼기]가 있고, 아니면 보기만
    if (state.admin) return listRow(path, onRemove);
    const row = el("div");
    row.insertAdjacentHTML("beforeend", FOLDER_ICON);
    row.append(el("span", {className: "mono", textContent: path}));
    return row;
  };
  $("blocked").replaceChildren(...state.blacklist.map((path) => adminRow(path, () => changeBlacklist(path, true))));
  $("blocked-empty").classList.toggle("hidden", state.blacklist.length > 0);
  $("not-admin-protect").classList.toggle("hidden", state.admin);
  $("protected").replaceChildren(...state.protected.map((path) => adminRow(path, () => changeProtect(path, true))));
  $("protected-empty").classList.toggle("hidden", state.protected.length > 0);
  showSteps();
  showRecognize();
  showView();
}

// ---------- 색인 단계: 순서(▲▼)·켜기는 바로 저장, [이것만 하기]는 그 단계만 돌린다 ----------
function showSteps() {
  const list = state.step_list;
  $("steps").replaceChildren(...list.map((step, i) => {
    const on = el("input", {type: "checkbox", checked: step.on, title: "끄면 [색인 갱신]에서 빠져요"});
    on.addEventListener("change", () => saveSteps(list.map((s, j) => (j === i ? {...s, on: on.checked} : s))));
    const move = (to, text, label) => {
      const b = el("button", {className: "btn s", textContent: text, title: label, disabled: to < 0 || to >= list.length});
      b.addEventListener("click", () => {
        const next = [...list];
        [next[i], next[to]] = [next[to], next[i]];
        saveSteps(next);
      });
      return b;
    };
    const alone = el("button", {className: "btn s", textContent: "이것만 하기"});
    alone.addEventListener("click", async () => {
      try {
        showIndex(await api("/api/index", {only: step.name}));
        pollIndex();
      } catch (e) {
        say("steps-message", e.message, "error");
      }
    });
    return el("div", {className: "step"}, on, el("span", {textContent: `${i + 1}. ${state.steps[step.name]}`}), move(i - 1, "▲", "앞으로"), move(i + 1, "▼", "뒤로"), alone);
  }));
  for (const name of ["video_interval", "video_similar"]) {
    Object.assign($(name), {min: state.numbers[name].min, max: state.numbers[name].max});
    setField(name, state.settings[name]);
  }
}

// ---------- 사진·동영상 인식: 모델 받기, 얼굴 찾기, 내 분류 모델 ----------
function showRecognize() {
  $("model-list").replaceChildren(...state.models.map((m) => el("div", {}, el("span", {className: "mono", textContent: `${m.title} · ${size(m.bytes)} · ${m.ready ? "받음" : "안 받음"}`}))));
  $("models-get").classList.toggle("hidden", state.models.every((m) => m.ready));
  $("face_on").checked = state.settings.face_on;
  $("custom-path").textContent = state.settings.custom_model_path || "고르지 않음";
  const labels = state.settings.custom_labels;
  if ($("custom-labels").querySelector("[data-edited]")) return;  // 이름·기준을 고치는 중이면 다시 그리지 않는다
  const field = (i, name, props) => {  // dataset은 읽기 전용이라 el()의 속성으로 넣을 수 없다.
    const input = el("input", props);
    Object.assign(input.dataset, {i, field: name});
    return input;
  };
  $("custom-labels").replaceChildren(...labels.map((label, i) => el("div", {className: "step"},
    field(i, "name", {type: "text", value: label.name, maxLength: 30}),
    el("span", {textContent: "기준"}), field(i, "threshold", {type: "number", min: 1, max: 100, value: label.threshold}), el("span", {textContent: "%"}),
    el("label", {}, field(i, "show", {type: "checkbox", checked: label.show}), " 칩으로 보기"))));
  $("custom-save-row").classList.toggle("hidden", !labels.length);
}

$("models-get").addEventListener("click", async (e) => {
  const button = e.currentTarget;
  const missing = state.models.filter((m) => !m.ready);
  const text = missing.map((m) => `${m.title} ${size(m.bytes)}`).join(", ");
  if (!(await ask({title: "모델을 받을까요?", text: `${text}\n받는 곳: huggingface.co(Xenova/clip-vit-base-patch32), github.com(opencv/opencv_zoo)\n받은 뒤 크기와 SHA-256을 확인해요.`}))) return;
  busy(button, "recognize-message", async () => {
    const result = await runJob((await api("/api/models/download", {groups: missing.map((m) => m.name)})).job);
    await loadState();
    say("recognize-message", `${result.done.length}개 받았어요.${failedText(result)}`, result.failed.length ? "error" : "ok");
  });
});

$("face_on").addEventListener("change", async (e) => {
  if (e.target.checked) await saveSettings({face_on: true}, "recognize-message");
  else await forgetFaces();
  await loadState();
});
$("faces-forget").addEventListener("click", forgetFaces);

async function forgetFaces() {
  if (!(await ask({title: "얼굴 정보를 지울까요?", text: "모아 둔 얼굴 지문과 ‘같은 사람’ 묶음을 지우고 얼굴 찾기를 꺼요. 사진은 그대로예요."}))) return loadState();
  try {
    state.settings = (await api("/api/faces/forget", {})).settings;
    say("recognize-message", "얼굴 정보를 지웠어요.", "ok");
  } catch (err) {
    say("recognize-message", err.message, "error");
  }
  await loadState();
}

async function setCustomModel(data) {
  try {
    await api("/api/custom_model", data);
    $("custom-labels").querySelectorAll("[data-edited]").forEach((input) => delete input.dataset.edited);  // 새 모델의 분류로 다시 그린다
    await loadState();
  } catch (err) {
    say("recognize-message", err.message, "error");
  }
}
$("custom-pick").addEventListener("click", async () => {
  const path = await pickPath({title: "내 분류 모델(.onnx) 고르기", ext: ".onnx"});
  if (path) await setCustomModel({path});
});
$("custom-clear").addEventListener("click", () => setCustomModel({path: ""}));
$("custom-save").addEventListener("click", async () => {
  const labels = state.settings.custom_labels.map((label) => ({...label}));
  $("custom-labels").querySelectorAll("input").forEach((input) => {
    const {i, field} = input.dataset;
    labels[i][field] = field === "show" ? input.checked : field === "threshold" ? Number(input.value) : input.value;
  });
  await saveAndSay({custom_labels: labels}, "recognize-message", "저장했어요.");
});

// 설정을 저장하고 다시 읽은 뒤 id 칸에 알린다(실패하면 saveSettings가 쓴 문구를 오류 색으로).
async function saveAndSay(changes, id, done) {
  if (await saveSettings(changes, id)) {
    await loadState();
    say(id, done, "ok");
  } else {
    $(id).className = "message error";
  }
}

async function saveSteps(list) {
  await saveAndSay({index_steps: list}, "steps-message", "색인 단계를 저장했어요.");
}

$("numbers-save").addEventListener("click", async () => {
  const changes = {video_interval: Number($("video_interval").value), video_similar: Number($("video_similar").value)};
  await saveAndSay(changes, "steps-message", "저장했어요. 장면 간격을 바꾸면 다음 [사진·동영상 비교]에서 장면을 다시 뽑아요.");
});

function showView() {
  document.querySelectorAll("input[name=list_group]").forEach((r) => { r.checked = r.value === state.settings.list_group; });
  $("pin_installers").checked = state.settings.pin_installers;
  $("show_same_name").checked = state.settings.show_same_name;
  $("update_check").checked = state.settings.update_check;
}

async function saveView(changes) {
  if (await saveSettings(changes, "view-message")) {
    say("view-message", "저장했어요.", "ok");
    loadFiles();
  } else {
    $("view-message").className = "message error";
  }
}
document.querySelectorAll("input[name=list_group]").forEach((r) => r.addEventListener("change", () => saveView({list_group: r.value})));
$("pin_installers").addEventListener("change", (e) => saveView({pin_installers: e.target.checked}));
$("show_same_name").addEventListener("change", async (e) => {
  await saveView({show_same_name: e.target.checked});
  if (picked && !picked.folder) document.querySelector('[data-fold="same-name"]').classList.toggle("hidden", !state.settings.show_same_name);  // 열린 상세에도 바로
});

$("update_check").addEventListener("change", async (e) => {
  if (await saveSettings({update_check: e.target.checked}, "update-message")) say("update-message", "저장했어요.", "ok");
  else $("update-message").className = "message error";
});

// ---------- 캐시 정리 ----------
// [살펴보기]의 답(cacheFound)으로 묶음(임시 폴더·브라우저·앱)마다 체크 목록을 그린다. 끈 캐시는 설정 cache_skip에 기억한다.
// 묶음의 체크는 그 안의 캐시를 모두 켜거나 끈다. 서버에는 고른 캐시의 이름만 보내고, 지울 것은 서버가 다시 찾는다.
let cacheFound = null;
const cacheOn = (p) => !state.settings.cache_skip.includes(p.id);
const cachePicked = () => (cacheFound ? cacheFound.places.filter((p) => p.found && !p.running && p.count && cacheOn(p)) : []);

function drawCache() {
  if (!cacheFound) return;
  const save = (ids, on) => saveSettings({cache_skip: on ? state.settings.cache_skip.filter((i) => !ids.includes(i)) : [...new Set([...state.settings.cache_skip, ...ids])]}, "cache-message");
  const lines = [];
  for (const [group, title] of Object.entries(cacheFound.groups)) {
    const places = cacheFound.places.filter((p) => p.group === group && p.found);
    if (!places.length) continue;
    const all = el("input", {type: "checkbox", checked: places.every(cacheOn)});
    all.indeterminate = !all.checked && places.some(cacheOn);
    all.addEventListener("change", () => save(places.map((p) => p.id), all.checked));
    lines.push(el("label", {className: "group"}, all, el("span", {textContent: title})));
    for (const p of places) {
      const box = el("input", {type: "checkbox", checked: cacheOn(p)});
      box.addEventListener("change", () => save([p.id], box.checked));
      const info = p.running ? "켜져 있어 건너뛰어요" : p.count ? `${size(p.bytes)} · ${p.count.toLocaleString()}개` : "오래된 것 없음";
      lines.push(el("label", {}, box, el("span", {textContent: p.name}), el("small", {textContent: info})));
    }
  }
  $("cache-list").replaceChildren(...(lines.length ? lines : [el("p", {className: "hint", textContent: "이 PC에서 찾은 캐시 폴더가 없어요."})]));
  const picked = cachePicked();
  $("cache-clean").disabled = !picked.length;
  $("cache-clean").textContent = picked.length ? `휴지통으로 보내기 (${size(picked.reduce((s, p) => s + p.bytes, 0))})` : "휴지통으로 보내기";
}

$("cache-scan").addEventListener("click", (e) => busy(e.target, "cache-message", async () => {
  if (Number($("cache_days").value) !== state.settings.cache_days && !(await saveSettings({cache_days: Number($("cache_days").value)}, "cache-message"))) return;
  cacheFound = await runJob((await api("/api/cache/scan", {})).job);
  say("cache-message", "");
  drawCache();
}));

$("cache-clean").addEventListener("click", (e) => busy(e.target, "cache-message", async () => {
  const picked = cachePicked();
  if (!picked.length) return say("cache-message", "보낼 캐시가 없어요. [살펴보기]를 다시 누르세요.");  // busy가 버튼을 다시 켜 둔다
  const count = picked.reduce((s, p) => s + p.count, 0);
  const text = `${picked.map((p) => p.name).join(", ")}\n\n${state.settings.cache_days}일 넘게 안 고친 ${count.toLocaleString()}개(${size(picked.reduce((s, p) => s + p.bytes, 0))})를 휴지통으로 보내요.\n휴지통에서 다시 꺼낼 수 있어요.`;
  if (!(await ask({title: "캐시를 휴지통으로 보낼까요?", text}))) return;
  const result = await runJob((await api("/api/cache/clean", {ids: picked.map((p) => p.id), confirm: true})).job);
  cacheFound = await runJob((await api("/api/cache/scan", {})).job);
  drawCache();
  say("cache-message", `${result.trashed.toLocaleString()}개를 휴지통으로 보냈어요.${failedText(result)}`, "ok");
}));

$("days-save").addEventListener("click", async () => {
  const changes = {recent_days: Number($("recent_days").value), stale_days: Number($("stale_days").value)};
  if (await saveSettings(changes, "days-message")) {
    say("days-message", "기간을 저장했어요.", "ok");
    await loadState();
  } else {
    $("days-message").className = "message error";
  }
});

$("reindex").addEventListener("click", async () => {
  try {
    showIndex(await api("/api/index", {}));
    pollIndex();
  } catch (e) {
    $("index-progress").textContent = e.message;
  }
});

// ---------- 시작 ----------
(async () => {
  showPage("files");
  await loadState();
  await loadFiles();
  const bounds = await windowCall("bounds");
  if (bounds) document.documentElement.classList.toggle("maximized", bounds.maximized);
  if (state.index.running) pollIndex();
  checkUpdate();
})();

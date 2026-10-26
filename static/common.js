"use strict";
// 본 창(app.js)과 잠금 창(lock.js)이 같이 쓰는 것: 요청·글자·크기 표기, 대화상자(확인·입력), 알림, 말풍선, 앱 안의 폴더·파일 고르기.
// 화면 공유에서 창 하나만 공유해도 보이게, 말풍선과 고르기는 창 안에 그린다(브라우저의 title 말풍선·Windows 고르기 창은 따로 뜨는 창이다).

const $ = (id) => document.getElementById(id);
const DRAG = "application/x-file-organizer";  // 본 창의 끌어 놓기로 넘기는 경로 목록의 형식
const FOLDER_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h4l2 2.5h7A2.5 2.5 0 0 1 21 9v8.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>';

// 화면 색: theme()이 주는 설정(auto면 Windows 설정을 따른다)대로 맞추는 함수를 돌려주고, Windows 색이 바뀌면 다시 맞춘다.
function followTheme(theme) {
  const query = matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    const chosen = theme() || "auto";
    document.documentElement.dataset.theme = chosen === "auto" ? (query.matches ? "dark" : "light") : chosen;
  };
  query.addEventListener("change", apply);
  return apply;
}

async function api(path, data) {
  const options = data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({error: `응답을 읽지 못했습니다 (${response.status})`}));
  if (!response.ok) throw new Error(body.error || String(response.status));
  return body;
}

// 요청하는 동안 버튼을 잠그고, 실패하면 messageId에 문구를 보여 준다.
async function busy(button, messageId, work) {
  button.disabled = true;
  try {
    await work();
  } catch (e) {
    say(messageId, e.message, "error");
  } finally {
    button.disabled = false;
  }
}

function say(id, text, kind = "") {
  $(id).textContent = text;
  $(id).className = "message " + kind;
}

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...kids.filter((kid) => kid !== null && kid !== undefined && kid !== false));
  return node;
}

// Windows 탐색기와 같은 크기 표기: 1,024바이트 미만은 바이트, 그 위는 KB·MB·GB·TB에 유효 숫자 세 자리(버림). 예: 88.0 KB, 1.20 MB, 182 MB
function size(bytes) {
  const units = ["KB", "MB", "GB", "TB"];
  if (!bytes || bytes < 1024) return `${bytes || 0}바이트`;
  let n = bytes / 1024, i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1024; i++; }
  const digits = n < 10 ? 2 : n < 100 ? 1 : 0;
  const cut = Math.floor(n * 10 ** digits) / 10 ** digits;
  return `${cut.toFixed(digits)} ${units[i]}`;
}

// 대화상자·알림·말풍선·고르기 창의 틀: 두 창이 같이 쓰도록 여기서 만든다.
document.body.append(
  el("div", {id: "dialog", className: "dialog hidden"},
    el("form", {id: "dialog-form", className: "dialog-box"},
      el("p", {id: "dialog-title", className: "dialog-title"}), el("p", {id: "dialog-text", className: "hint"}),
      el("input", {type: "text", id: "dialog-input", className: "hidden", autocomplete: "off"}),
      el("div", {className: "row end"}, el("button", {type: "button", id: "dialog-cancel", className: "btn", textContent: "취소"}),
        el("button", {id: "dialog-ok", className: "btn p", textContent: "확인"})))),
  el("div", {id: "picker", className: "dialog hidden"},
    el("div", {className: "dialog-box picker"},
      el("p", {id: "picker-title", className: "dialog-title"}),
      el("div", {className: "row"}, el("button", {type: "button", id: "picker-up", className: "btn s", textContent: "↑ 위로"}),
        el("input", {type: "text", id: "picker-path", placeholder: "주소를 넣고 Enter"})),
      el("div", {id: "picker-list", className: "list picker-list"}),
      el("p", {id: "picker-message", className: "message"}),
      el("div", {className: "row end"}, el("button", {type: "button", id: "picker-new", className: "btn s", textContent: "새 폴더"}),
        el("span", {className: "grow"}), el("button", {type: "button", id: "picker-cancel", className: "btn", textContent: "취소"}),
        el("button", {type: "button", id: "picker-ok", className: "btn p", textContent: "이 폴더 고르기"})))),
  el("div", {id: "tip", className: "tip hidden"}),
  el("div", {id: "toast", className: "toast"}),
);

let toastTimer;
function toast(text) {
  $("toast").textContent = text;
  $("toast").classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("on"), 3000);
}

// 대화상자: 입력(value가 있으면)이나 확인. 확인하면 입력값 또는 true, 취소하면 null. secret이면 비밀번호처럼 가린다.
// ok·cancel: 버튼 글자. long: 긴 글(릴리스 노트)을 줄바꿈 그대로, 넘치면 스크롤. 글은 늘 textContent로 넣는다(HTML로 읽지 않는다).
function ask({title, text = "", value, secret = false, ok = "확인", cancel = "취소", long = false}) {
  $("dialog-title").textContent = title;
  $("dialog-ok").textContent = ok;
  $("dialog-cancel").textContent = cancel;
  $("dialog-text").textContent = text;
  $("dialog-text").classList.toggle("long", long);
  $("dialog-form").classList.toggle("wide", long);
  const input = $("dialog-input");
  input.type = secret ? "password" : "text";
  input.classList.toggle("hidden", value === undefined);
  $("dialog").classList.remove("hidden");
  if (value !== undefined) {
    input.value = value;
    input.focus();
    const dot = value.lastIndexOf(".");
    input.setSelectionRange(0, dot > 0 ? dot : value.length);  // 확장자 앞까지 골라 둔다.
  } else {
    $("dialog-ok").focus();
  }
  return new Promise((resolve) => {
    const finish = (answer) => {
      $("dialog").classList.add("hidden");
      $("dialog-form").onsubmit = $("dialog-cancel").onclick = null;
      resolve(answer);
    };
    $("dialog-form").onsubmit = (e) => { e.preventDefault(); finish(value === undefined ? true : input.value); };
    $("dialog-cancel").onclick = () => finish(null);
  });
}

// 앱 안의 고르기 창: 폴더(ext 없이) 또는 ext로 끝나는 파일. 고른 경로나 취소하면 null. 서버의 /api/browse가 목록을 준다.
function pickPath({title = "폴더 고르기", ext = "", start = ""} = {}) {
  let here = "";
  const show = async (path) => {
    try {
      const found = await api(`/api/browse?path=${encodeURIComponent(path)}&ext=${encodeURIComponent(ext)}`);
      here = found.path;
      $("picker-path").value = here;
      $("picker-up").disabled = !here;
      $("picker-new").disabled = !here;
      $("picker-ok").disabled = !!ext || !here;
      say("picker-message", "");
      const rows = [...found.folders.map((f) => ({...f, folder: true})), ...found.files];
      $("picker-list").replaceChildren(...(rows.length ? rows.map((item) => {
        const row = el("button", {type: "button", className: "picker-row" + (item.folder ? "" : " file")});
        if (item.folder) row.insertAdjacentHTML("beforeend", FOLDER_ICON);
        row.append(el("span", {textContent: item.name}), item.folder ? null : el("small", {textContent: size(item.size)}));
        row.addEventListener("click", () => (item.folder ? show(item.path) : finish(item.path)));
        return row;
      }) : [el("p", {className: "hint", textContent: ext ? `폴더나 ${ext} 파일이 없어요.` : "아래 폴더가 없어요."})]));
      $("picker-up").onclick = () => show(found.parent || "");
    } catch (e) {
      say("picker-message", e.message, "error");
    }
  };
  let finish;
  const done = new Promise((resolve) => {
    finish = (answer) => {
      $("picker").classList.add("hidden");
      for (const id of ["picker-ok", "picker-cancel", "picker-new", "picker-up"]) $(id).onclick = null;
      $("picker-path").onkeydown = null;
      resolve(answer);
    };
  });
  $("picker-title").textContent = title;
  $("picker-ok").textContent = ext ? "파일을 누르세요" : "이 폴더 고르기";
  $("picker-ok").onclick = () => finish(here);
  $("picker-cancel").onclick = () => finish(null);
  $("picker-new").onclick = async () => {
    const name = await ask({title: "새 폴더", text: here, value: "새 폴더"});
    if (!name) return;
    try {
      await api("/api/mkdir", {path: here, name});
      await show(here);
    } catch (e) {
      say("picker-message", e.message, "error");
    }
  };
  $("picker-path").onkeydown = (e) => { if (e.key === "Enter") show($("picker-path").value.trim()); };
  $("picker").classList.remove("hidden");
  show(start);
  return done;
}

// 바깥(탐색기)에서 파일을 끌어 와 창에 놓아도 그 파일이 열리지 않게 한다(잠금 창도: 놓으면 file:// 로 넘어가 잠금 화면이 사라졌다).
// 본 창 안의 끌기(DRAG)는 막지 않는다: 놓을 곳(app.js의 dropTarget)만 받고, 그 밖에 놓으면 아무 일도 없다.
document.addEventListener("dragover", (e) => { if (!e.dataTransfer.types.includes(DRAG)) e.preventDefault(); });
document.addEventListener("drop", (e) => e.preventDefault());

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("dialog").classList.contains("hidden")) $("dialog-cancel").click();
  else if (!$("picker").classList.contains("hidden")) $("picker-cancel").click();
});

// 말풍선: title 속성을 가진 요소에 마우스를 올리면 브라우저의 말풍선(따로 뜨는 창) 대신 창 안에 그린다.
// 처음 올릴 때 title을 data-tip으로 옮겨 브라우저 말풍선이 뜨지 않게 한다(코드가 나중에 title을 바꿔도 다음에 올릴 때 다시 옮긴다).
let tipTimer = null;
document.addEventListener("mouseover", (e) => {
  const target = e.target.closest?.("[title], [data-tip]");
  if (!target) return;
  if (target.title) {
    target.dataset.tip = target.title;
    target.removeAttribute("title");
  }
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => {
    const tip = $("tip");
    tip.textContent = target.dataset.tip;
    tip.classList.remove("hidden");
    const box = target.getBoundingClientRect();
    const below = box.bottom + 6 + tip.offsetHeight < innerHeight;
    tip.style.left = Math.max(4, Math.min(innerWidth - tip.offsetWidth - 4, box.left)) + "px";
    tip.style.top = (below ? box.bottom + 6 : box.top - tip.offsetHeight - 6) + "px";
  }, 500);
});
const hideTip = () => {
  clearTimeout(tipTimer);
  $("tip").classList.add("hidden");
};
document.addEventListener("mouseout", (e) => { if (e.target.closest?.("[data-tip]")) hideTip(); });
document.addEventListener("mousedown", hideTip);
document.addEventListener("scroll", hideTip, true);

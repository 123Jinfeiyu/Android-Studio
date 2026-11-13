/* Android Studio — 交互层（零依赖原生 JS）
   对应 iPhone-Studio 的 app.js：
   - 画面档位 + MJPEG、坐标映射（含旋转预览逆映射）
   - 四栏：编辑器 / 脚本库 / 元素树 / API 速查
   - SSE 实时日志、演示模式、全屏 / 铺满、明暗主题
*/

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const state = {
  dev: null,            // /api/status 结果
  rotView: 0,           // 画面旋转预览 0/90/270
  natural: [1080, 2340],// 最近一帧真实尺寸 (px)
  gear: "0.5",          // 画面档位
  timer: null,
  fill: false,
};

/* ───────────────────────── 工具 ───────────────────────── */

async function jfetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

function log(line) {
  const el = $("#logs");
  el.textContent += line + "\n";
  el.scrollTop = el.scrollHeight;
}

/* ───────────────────────── 连接状态 ───────────────────────── */

async function pollStatus() {
  try {
    const s = await jfetch("/api/status");
    const changed = JSON.stringify(s) !== JSON.stringify(state.dev);
    state.dev = s;
    renderStatus();
    if (changed && s.online) refreshScreen(true);
  } catch (e) {
    state.dev = { online: false, demo: true };
    renderStatus();
  }
}

function renderStatus() {
  const s = state.dev;
  const pill = $("#conn-pill"), txt = $("#conn-text"), meta = $("#dev-meta");
  if (s.online) {
    pill.classList.add("on"); pill.classList.remove("off");
    txt.textContent = "已连接";
    meta.textContent = `${s.model || ""} · Android ${s.android || "?"} · ${s.size ? s.size[0] + "×" + s.size[1] : "?"}px · 旋转${s.rotation}° · 电量${s.battery}%`;
  } else {
    pill.classList.add("off"); pill.classList.remove("on");
    txt.textContent = "演示模式";
    meta.textContent = s.error ? s.error : "未检测到设备（USB 调试 / adb connect）";
  }
}

$("#conn-pill").onclick = async () => {
  log("⟳ 手动重新连接…");
  await pollStatus();
  refreshScreen(true);
};

/* ───────────────────────── 画面 ───────────────────────── */

function setGear(g) {
  state.gear = g;
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  const img = $("#screen");
  if (g === "off") { img.removeAttribute("src"); $("#frame-info").textContent = "画面关闭"; return; }
  if (g === "mjpeg") {
    img.src = `/api/stream?interval=0.1&t=${Date.now()}`;
    $("#frame-info").textContent = "MJPEG 流";
    return;
  }
  refreshScreen(true);
  state.timer = setInterval(() => refreshScreen(), parseFloat(g) * 1000);
}

async function refreshScreen(force) {
  const img = $("#screen");
  try {
    const r = await fetch(`/api/screen?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    img.src = URL.createObjectURL(blob);
    const size = (r.headers.get("X-Screen-Size") || "").split("x").map(Number);
    if (size.length === 2 && size[0]) {
      const changed = size[0] !== state.natural[0] || size[1] !== state.natural[1];
      state.natural = size;
      if (changed || force) layoutScreen();
    }
    $("#frame-info").textContent =
      `${state.natural[0]}×${state.natural[1]}px` +
      (r.headers.get("X-Frame-Cached") === "true" ? " (缓存帧)" : "");
  } catch (e) {
    $("#frame-info").textContent = "抓帧失败: " + e.message;
  }
}

function layoutScreen() {
  const img = $("#screen");
  const wrap = $("#screen-wrap");
  const [W, H] = state.natural;
  const rot = state.rotView;
  const WW = wrap.clientWidth - 16, WH = wrap.clientHeight - 16;
  if (!WW || !WH) return;
  const s = rot === 0
    ? Math.min(WW / W, WH / H)
    : Math.min(WW / H, WH / W);
  img.style.width = (W * s) + "px";
  img.style.height = (H * s) + "px";
  img.style.position = "absolute";
  img.style.left = "50%"; img.style.top = "50%";
  img.style.transformOrigin = "center";
  img.style.transform =
    `translate(-50%, -50%) rotate(${rot === 90 ? 90 : rot === 270 ? -90 : 0}deg)`;
  document.body.dataset.rot = rot === 270 ? "270" : "";
}

/* 点击 / 拖拽 → 设备坐标（旋转预览时逆映射） */
function toDevice(vx, vy, box) {
  const [W, H] = state.natural;
  const rot = state.rotView;
  if (rot === 0) return [vx / box.scale, vy / box.scale];
  if (rot === 90) return [vy / box.scale, H - vx / box.scale];
  return [W - vy / box.scale, vx / box.scale];
}

function viewBox() {
  const img = $("#screen");
  const r = img.getBoundingClientRect();
  const rot = state.rotView;
  // getBoundingClientRect 是旋转后的包围盒
  return {
    left: r.left, top: r.top,
    w: rot === 0 ? r.width : r.height,
    h: rot === 0 ? r.height : r.width,
    scale: rot === 0 ? state.natural[0] / r.width : state.natural[1] / r.height,
  };
}

let drag = null;
const img = $("#screen");
img.addEventListener("pointerdown", (e) => {
  if (!state.dev || !state.dev.online) return;
  drag = { x: e.clientX, y: e.clientY, t: Date.now() };
  img.setPointerCapture(e.pointerId);
});
img.addEventListener("pointerup", async (e) => {
  if (!drag || !state.dev || !state.dev.online) { drag = null; return; }
  const t0 = drag.t;
  const box = viewBox();
  const p1 = toDevice(drag.x - box.left, drag.y - box.top, box);
  const p2 = toDevice(e.clientX - box.left, e.clientY - box.top, box);
  const dist = Math.hypot(e.clientX - drag.x, e.clientY - drag.y);
  drag = null;
  try {
    if (dist < 6) {
      await jfetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "tap", x: p1[0], y: p1[1] }) });
    } else {
      await jfetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "swipe", x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], duration: Math.min(0.8, Math.max(0.15, (Date.now() - t0) / 1000)) }) });
    }
    setTimeout(() => refreshScreen(), 200);
  } catch (err) { log("✗ 动作失败: " + err.message); }
});
img.addEventListener("dblclick", async (e) => {
  if (!state.dev || !state.dev.online) return;
  const box = viewBox();
  const [x, y] = toDevice(e.clientX - box.left, e.clientY - box.top, box);
  await jfetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "double_tap", x, y }) });
});
window.addEventListener("resize", layoutScreen);

/* 档位与工具按钮 */
$("#fps-gear").onchange = (e) => setGear(e.target.value);
$("#btn-refresh").onclick = () => refreshScreen(true);
$("#btn-save").onclick = async () => {
  const a = document.createElement("a");
  a.href = $("#screen").src; a.download = `as_${Date.now()}.jpg`; a.click();
};
$("#btn-full").onclick = () => {
  document.body.classList.toggle("full");
  setTimeout(layoutScreen, 60);
};
$("#btn-fill").onclick = () => {
  $("#screen-wrap").classList.toggle("fill");
  layoutScreen();
};
$("#btn-rotate-view").onclick = () => {
  state.rotView = state.rotView === 90 ? 270 : state.rotView === 270 ? 0 : 90;
  layoutScreen();
  log(`↻ 画面旋转预览: ${state.rotView}°（坐标已逆映射）`);
};

/* ───────────────────────── 左栏控制 ───────────────────────── */

async function act(body) {
  try { return await jfetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch (e) { log("✗ " + e.message); }
}

$$(".dir-btn").forEach((b) => b.onclick = () => act({ type: "swipe_dir", dir: b.dataset.dir }));
$$("[data-key]").forEach((b) => b.onclick = () => act({ type: "key", keycode: +b.dataset.key }));
$("#btn-wake").onclick = () => act({ type: "wake" });
$("#btn-lock").onclick = () => act({ type: "lock" });
$("#btn-unlock").onclick = () => act({ type: "unlock" });
$("#btn-notify").onclick = () => act({ type: "key", keycode: 83 });  // KEYCODE_NOTIFICATION? 用展开通知 83
$("#btn-portrait").onclick = () => act({ type: "rotate", mode: 0 });
$("#btn-landscape").onclick = () => act({ type: "rotate", mode: 1 });

$("#btn-send-text").onclick = sendText;
$("#txt-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendText(); });
async function sendText() {
  const v = $("#txt-input").value.trim();
  if (!v) return;
  await act({ type: "text", text: v });
  log(`⌨ 文本已发送: ${v}`);
}

/* 应用管理 */
$("#btn-launch").onclick = async () => {
  const pkg = $("#pkg-input").value.trim();
  if (!pkg) return;
  try { await jfetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ package: pkg }) }); $("#pkg-out").textContent = "✓ 已启动 " + pkg; }
  catch (e) { $("#pkg-out").textContent = "✗ " + e.message; }
};
$("#btn-terminate").onclick = async () => {
  const pkg = $("#pkg-input").value.trim();
  if (!pkg) return;
  try { await jfetch("/api/terminate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ package: pkg }) }); $("#pkg-out").textContent = "✓ 已结束 " + pkg; }
  catch (e) { $("#pkg-out").textContent = "✗ " + e.message; }
};
$("#btn-foreground").onclick = async () => {
  try { const r = await jfetch("/api/foreground"); $("#pkg-out").textContent = JSON.stringify(r.foreground, null, 1); } catch (e) { $("#pkg-out").textContent = "✗ " + e.message; }
};
$("#btn-apps").onclick = async () => {
  try {
    const r = await jfetch("/api/apps");
    $("#pkg-out").textContent = r.apps.join("\n") + `\n（共 ${r.apps.length} 个第三方应用）`;
  } catch (e) { $("#pkg-out").textContent = "✗ " + e.message; }
};
$("#btn-kill-bg").onclick = async () => {
  if (!confirm("清理后台将结束缓存的后台进程（不杀前台），继续？")) return;
  try { const r = await jfetch("/api/kill_background", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }); $("#pkg-out").textContent = "✓ 已执行 am kill-all\n" + JSON.stringify(r, null, 1); }
  catch (e) { $("#pkg-out").textContent = "✗ " + e.message; }
};

/* APK 安装 */
$("#btn-install").onclick = async () => {
  const f = $("#apk-file").files[0];
  if (!f) { $("#apk-out").textContent = "先选择 .apk 文件"; return; }
  $("#apk-out").textContent = `上传中 ${f.name} (${(f.size / 1e6).toFixed(1)} MB)…`;
  const fd = new FormData(); fd.append("file", f);
  try {
    const r = await jfetch("/api/install", { method: "POST", body: fd });
    $("#apk-out").textContent = (r.ok ? "✓ 安装成功\n" : "✗ 安装失败\n") + r.output;
  } catch (e) { $("#apk-out").textContent = "✗ " + e.message; }
};

/* ───────────────────────── Tabs ───────────────────────── */

$$(".tab").forEach((t) => t.onclick = () => {
  $$(".tab").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $$(".tabpane").forEach((p) => p.classList.add("hidden"));
  $("#tab-" + t.dataset.tab).classList.remove("hidden");
});

/* ───────────────────────── 编辑器 / 运行 ───────────────────────── */

const editor = $("#editor");
editor.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runScript(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveScript(); }
  if (e.key === "Tab") {
    e.preventDefault();
    const s = editor.selectionStart;
    editor.setRangeText("    ", s, editor.selectionEnd, "end");
  }
});

$("#btn-run").onclick = runScript;
$("#btn-stop").onclick = stopScript;

async function runScript() {
  const code = editor.value;
  const name = $("#script-name").value.trim() || "<inline>";
  try {
    await jfetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code, name }) });
    log(`▶ 已提交运行: ${name}`);
  } catch (e) { log("✗ " + e.message); }
}
async function stopScript() {
  try { await jfetch("/api/stop", { method: "POST" }); log("■ 已请求停止"); } catch (e) { log("✗ " + e.message); }
}

$("#btn-save-script").onclick = saveScript;
async function saveScript() {
  const name = $("#script-name").value.trim();
  if (!name.endsWith(".py")) { log("✗ 文件名需以 .py 结尾"); return; }
  try {
    await jfetch(`/api/scripts/${encodeURIComponent(name)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: editor.value }) });
    loadScriptList();
  } catch (e) { log("✗ " + e.message); }
}

/* ───────────────────────── 脚本库 ───────────────────────── */

async function loadScriptList() {
  try {
    const r = await jfetch("/api/scripts");
    const el = $("#script-list");
    el.innerHTML = "";
    r.scripts.forEach((s) => {
      const div = document.createElement("div");
      div.className = "script-item";
      div.innerHTML = `<span class="name">${s.name}</span>
        <button class="primary">载入</button><button class="danger">删除</button>`;
      const [bLoad, bDel] = div.querySelectorAll("button");
      bLoad.onclick = async () => {
        const d = await jfetch(`/api/scripts/${encodeURIComponent(s.name)}`);
        editor.value = d.code;
        $("#script-name").value = s.name;
        $$(".tab").forEach((x) => x.classList.remove("active"));
        document.querySelector('.tab[data-tab="editor"]').classList.add("active");
        $("#tab-scripts").classList.add("hidden");
        $("#tab-editor").classList.remove("hidden");
      };
      bDel.onclick = async () => {
        if (!confirm(`删除 ${s.name}?`)) return;
        await fetch(`/api/scripts/${encodeURIComponent(s.name)}`, { method: "DELETE" });
        loadScriptList();
      };
      el.appendChild(div);
    });
  } catch (e) { log("✗ 脚本库加载失败: " + e.message); }
}

/* ───────────────────────── 元素树 ───────────────────────── */

$("#btn-tree").onclick = async () => {
  $("#tree-out").textContent = "抓取中…（uiautomator dump 约需 1–3 秒）";
  try {
    const r = await jfetch("/api/tree");
    $("#tree-out").textContent = prettyXml(r.xml) || "(空)";
  } catch (e) { $("#tree-out").textContent = "✗ " + e.message; }
};

function prettyXml(xml) {
  try {
    const doc = new DOMParser().parseFromString(xml, "text/xml");
    if (doc.querySelector("parsererror")) return xml;
    const lines = [];
    (function walk(node, depth) {
      if (node.nodeType !== 1) return;
      const attrs = [];
      for (const a of node.attributes) attrs.push(`${a.name}=${JSON.stringify(a.value)}`);
      lines.push("  ".repeat(depth) + `<${node.tagName} ` + attrs.filter(a => a.startsWith("text=") || a.startsWith("resource-id=") || a.startsWith("bounds=") || a.startsWith("class=") || a.startsWith("clickable=")).join(" ") + ">");
      for (const c of node.children) walk(c, depth + 1);
    })(doc.documentElement, 0);
    return lines.join("\n");
  } catch (e) { return xml; }
}

/* ───────────────────────── API 速查 ───────────────────────── */

const API_REF = `在线脚本 API（注入 device / d / sleep / log / adb / time / json / os / Path）
print() 与 log() 都会写进日志面板；循环里用 device.sleep() 以便停止。

tap(x, y)                  按 px 坐标轻触
double_tap(x, y)           双击（两次快速 tap）
swipe(x1, y1, x2, y2, duration=0.3)
swipe_dir("up|down|left|right", duration=0.35, margin=0.22)
swipe_up() / swipe_down() / swipe_left() / swipe_right()
text("中文")               发送文本（中文需 ADBKeyboard）
key(keycode)               按键，如 key(3)=HOME key(4)=返回
home() back() recents() wake() lock() unlock()
launch(package)            monkey 拉起应用主 Activity
terminate(package)         am force-stop
app_current()              前台应用 {package, activity}
apps()                     已装第三方应用列表
background_apps()          后台缓存进程列表
kill_background(dry_run=False, keep_foreground=False)
tree()                     uiautomator 元素树 XML
window_size()              实时屏幕尺寸 (w, h) px
rotation() / set_orientation("auto"|0|1|3)
screenshot(name)           存到 shots/，返回路径
install(apk_path) / uninstall(package)
shell(cmd)                 高级用法：直接执行 adb shell
device.online / device.serial
sleep(t)                   可中断延时（循环里请用它）`;
$("#api-ref").textContent = API_REF;

/* ───────────────────────── 日志 SSE ───────────────────────── */

function connectLogs() {
  const es = new EventSource("/api/logs");
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    if (d.reset) $("#logs").textContent = "";
    (d.lines || []).forEach(log);
  };
  es.onerror = () => { es.close(); setTimeout(connectLogs, 3000); };
}

/* ───────────────────────── 主题 / 帮助 ───────────────────────── */

$("#btn-theme").onclick = () => {
  document.body.dataset.theme = document.body.dataset.theme === "dark" ? "light" : "dark";
};
$("#btn-help").onclick = () => $("#modal-help").classList.remove("hidden");
$("#btn-close-help").onclick = () => $("#modal-help").classList.add("hidden");
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal-help").classList.add("hidden"); });

/* ───────────────────────── 启动 ───────────────────────── */

(async function init() {
  await pollStatus();
  await loadDefaultScript();
  loadScriptList();
  connectLogs();
  setGear("0.5");
  setInterval(pollStatus, 6000);
})();

async function loadDefaultScript() {
  try {
    const d = await jfetch("/api/scripts/" + encodeURIComponent("01_设备体检.py"));
    editor.value = d.code;
  } catch (e) {
    editor.value = '# 连接设备后按 Ctrl+Enter 运行\nprint("设备在线:", device.online)\nprint("前台应用:", device.app_current())\nprint("屏幕尺寸(px):", device.window_size())\nprint("截图已保存:", device.screenshot("体检截图"))\n';
  }
}

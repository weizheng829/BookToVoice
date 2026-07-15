// 打包下载：用 fetch 以便无音频时弹窗提示，而不是跳转到 JSON 错误页
window.downloadBook = async function (btn, bookId) {
  if (btn && btn.disabled) return; // 防重复点击
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "打包中…"; }
  try {
    const res = await fetch(`/books/${bookId}/download`);
    if (!res.ok) {
      let msg = "下载失败";
      try { const d = await res.json(); msg = d.detail || msg; } catch (e) {}
      alert(msg);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const sanitize = (s) => (s || "").replace(/[\\/:*?"<>|]+/g, "_").trim();
    let fname = btn && btn.dataset.name ? sanitize(btn.dataset.name) : "";
    if (!fname) fname = `book_${bookId}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = `${fname}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("下载失败：" + e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
};

// 试听：用当前表单选中的声音 + 试听文本合成一段，在线播放并可下载
window.previewVoice = async function (btn) {
  const form = btn.closest("form");
  const voiceEl = form.querySelector("[name=voice]");
  const textEl = form.querySelector(".preview-text");
  const audio = form.querySelector(".preview-audio");
  const dl = form.querySelector(".preview-download");
  if (!voiceEl || !textEl) { alert("未找到声音/文本输入"); return; }
  const voice = voiceEl.value;
  const rateEl = form.querySelector("[name=rate]");
  const rate = rateEl ? rateEl.value : "+0%";
  const text = textEl.value;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "合成中…";
  try {
    const fd = new FormData();
    fd.append("text", text);
    fd.append("voice", voice);
    fd.append("rate", rate);
    const res = await fetch("/api/preview", { method: "POST", body: fd });
    if (!res.ok) {
      let msg = "试听失败";
      try { const d = await res.json(); msg = d.detail || msg; } catch (e) {}
      alert(msg);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    audio.src = url;
    audio.style.display = "";
    audio.load();
    dl.href = url;
    dl.download = "preview.mp3";
    dl.style.display = "";
    audio.play().catch(() => { /* 浏览器拦截自动播放时，用户可手动点播放器 */ });
  } catch (e) {
    alert("试听失败：" + e);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
};

// 拖拽上传初始化（新建书页 #drop-zone 存在时生效）
(function () {
  const dz = document.getElementById("drop-zone");
  if (!dz) return;
  const input = document.getElementById("file-input");
  const nameEl = document.getElementById("file-name");
  const show = () => {
    if (!input || !input.files.length) { if (nameEl) nameEl.textContent = ""; return; }
    nameEl.textContent = input.files[0].name;
    // 书名为空时自动用文件名（去扩展名）填充
    const nameInput = document.querySelector("[name=name]");
    if (nameInput && !nameInput.value.trim()) {
      nameInput.value = input.files[0].name.replace(/\.[^.]+$/, "");
    }
  };
  dz.addEventListener("click", () => input && input.click());
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    if (e.dataTransfer.files && e.dataTransfer.files.length && input) {
      input.files = e.dataTransfer.files;
      show();
    }
  });
  if (input) input.addEventListener("change", show);
})();

// 语速拖拉条初始化（.rate-control 存在时生效；range/number/hidden 联动）
(function () {
  document.querySelectorAll(".rate-control").forEach((rc) => {
    const field = rc.closest(".field");
    if (!field) return;
    const range = rc.querySelector(".rate-range");
    const num = rc.querySelector(".rate-num");
    const hidden = field.querySelector("input[name=rate]");
    const display = field.querySelector(".rate-display");
    const init = parseInt((hidden.value || "+0%").replace("%", "")) || 0;
    range.value = init; num.value = init;
    const fmt = (v) => (v >= 0 ? "+" : "") + v + "%";
    const update = (v) => {
      v = Math.max(-100, Math.min(100, parseInt(v) || 0));
      range.value = v; num.value = v;
      hidden.value = fmt(v);
      if (display) display.textContent = fmt(v);
    };
    range.addEventListener("input", () => update(range.value));
    num.addEventListener("input", () => update(num.value));
  });
})();

// 详情页：每 3 秒轮询进度，更新进度条与章节状态；章节完成即插入试听链接；全部结束后停止。
(function () {
  "use strict";
  const LABEL = { done: "已完成", running: "生成中", generating: "生成中", pending: "待处理", failed: "失败" };
  const CLS = { done: "badge ok", running: "badge run", generating: "badge run", pending: "badge wait", failed: "badge fail" };
  const id = window.BOOK_ID;
  if (!id) return;

  const rowMap = {};
  document.querySelectorAll("#chapters tbody tr").forEach((tr) => {
    rowMap[tr.dataset.id] = tr;
  });

  let timer = null;

  async function tick() {
    try {
      const res = await fetch(`/api/books/${id}`, { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      const p = data.progress;

      const bar = document.getElementById("bar");
      const txt = document.getElementById("prog-text");
      if (bar) bar.style.width = p.percent + "%";
      if (txt) {
        let s = `${p.done}/${p.total} 完成（${p.percent}%）`;
        if (p.failed) s += ` · ${p.failed} 失败`;
        txt.textContent = s;
      }

      for (const c of data.chapters) {
        const tr = rowMap[c.id];
        if (!tr) continue;
        const cell = tr.querySelector(".status-cell");
        if (cell) {
          cell.innerHTML = `<span class="${CLS[c.status] || "badge"}">${LABEL[c.status] || c.status}</span>`;
        }
        // 章节完成且操作列还没有试听链接 → 动态插入（无需刷新整页）
        const ops = tr.querySelector(".ops");
        if (c.status === "done" && ops && !ops.querySelector("a.link")) {
          const a = document.createElement("a");
          a.className = "link";
          a.href = `/books/${id}/chapters/${c.id}/audio`;
          a.textContent = "▶ 试听/下载";
          ops.insertBefore(a, ops.firstChild);
        }
      }

      // 全部结束 → 停止轮询（试听链接已动态插入，无需 reload）
      if (p.pending === 0 && p.generating === 0) {
        if (timer) { clearInterval(timer); timer = null; }
      }
    } catch (e) {
      /* 网络抖动，忽略 */
    }
  }

  timer = setInterval(tick, 3000);
  tick();
})();

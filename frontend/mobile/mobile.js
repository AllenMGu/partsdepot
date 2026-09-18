/* ============ WMS 手机端（H5）共享脚本 ============
 * 功能对标 wechat-miniprogram 小程序（登录/首页/申请/库存/扫码/日志/单据/盘点）。
 * 约定（与仓库 CSP 策略一致）：
 *   - 零内联事件处理器：统一事件委托（data-act / data-scan）；
 *   - 零内联脚本：本页所有逻辑均为 external script；
 *   - API 基址：页面位于 /mobile/*.html，相对基址 ../api（部署在任意子路径均可）。
 * 鉴权：与桌面端一致——/token 登录后由服务端下发 HttpOnly Cookie 会话，
 *       本地仅保存 user 快照与过期时间（键名与 frontend/common.js 相同，便于同域共享上下文）。
 * 页面脚本约定：在 mobile.js 之前加载，向 window.M_PAGES 注册 {pageName: initFn}；
 *              mobile.js 在 window load 事件后调用对应 initFn。
 */
(function () {
"use strict";

/* ---------------- 基础工具 ---------------- */
var API_BASE = (function () {
  try {
    var u = new URL("../api", window.location.href);
    return u.href.replace(/\/+$/, "");
  } catch (e) { return "/api"; }
})();

function esc(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function fmtNum(v) {
  var n = Number(v);
  if (!isFinite(n)) return "0.00";
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtDT(v) {
  if (!v) return "-";
  var d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  function p(x) { return (x < 10 ? "0" : "") + x; }
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
    " " + p(d.getHours()) + ":" + p(d.getMinutes());
}
function $(sel, root) { return (root || document).querySelector(sel); }
function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

/* ---------------- 鉴权（与桌面端 common.js 共享键名） ---------------- */
var AUTH = {
  getUser: function () {
    try { return JSON.parse(localStorage.getItem("user") || "null"); } catch (e) { return null; }
  },
  getExpiry: function () { return localStorage.getItem("token_expiry") || ""; },
  isLoggedIn: function () {
    var u = this.getUser();
    if (!u) return false;
    var ex = this.getExpiry();
    return !ex || new Date().getTime() < new Date(ex).getTime();
  },
  save: function (user, expiry) {
    if (user) localStorage.setItem("user", JSON.stringify(user));
    if (expiry) localStorage.setItem("token_expiry", String(expiry));
  },
  clear: function () {
    localStorage.removeItem("user");
    localStorage.removeItem("token_expiry");
  },
  require: function () {
    if (!this.isLoggedIn()) {
      window.location.href = "login.html";
      return false;
    }
    return true;
  },
  currentWarehouse: function () {
    var u = this.getUser() || {};
    var id = u.current_warehouse_id;
    var list = u.warehouses || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return u.current_warehouse_name ? { id: id, name: u.current_warehouse_name } : (list[0] || null);
  }
};

/* ---------------- API ---------------- */
function apiErrorDetail(data, status) {
  if (data && data.detail) {
    var d = data.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) {
      return d.map(function (x) { return (x && x.msg) || JSON.stringify(x); }).join("；");
    }
    return JSON.stringify(d);
  }
  return "请求失败（HTTP " + status + "）";
}

function api(method, path, body, opts) {
  opts = opts || {};
  var headers = {};
  if (body != null) headers["content-type"] = "application/json";
  var url = /^https?:/.test(path) ? path : API_BASE + path;
  return fetch(url, {
    method: method,
    headers: headers,
    credentials: "same-origin",
    body: body != null ? JSON.stringify(body) : undefined
  }).then(function (res) {
    return res.json().catch(function () { return null; }).then(function (data) {
      if (!res.ok) {
        if (res.status === 401 && opts.auth !== false && !/login\.html$/.test(location.pathname)) {
          AUTH.clear();
          window.location.href = "login.html";
        }
        var err = new Error(apiErrorDetail(data, res.status));
        err.status = res.status;
        throw err;
      }
      return data;
    });
  });
}

function apiForm(method, path, form) {
  // form-urlencoded（登录端点契约）
  var qs = Object.keys(form).map(function (k) {
    return encodeURIComponent(k) + "=" + encodeURIComponent(form[k]);
  }).join("&");
  var url = API_BASE + path;
  return fetch(url + (method === "GET" ? "?" + qs : ""), {
    method: method,
    credentials: "same-origin",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: method === "GET" ? undefined : qs
  }).then(function (res) {
    return res.json().catch(function () { return null; }).then(function (data) {
      if (!res.ok) {
        if (res.status === 401 && !/login\.html$/.test(location.pathname)) {
          AUTH.clear();
          window.location.href = "login.html";
        }
        throw new Error(apiErrorDetail(data, res.status));
      }
      return data;
    });
  });
}

/* ---------------- Toast / Modal ---------------- */
var toastTimer = null;
function toast(msg, kind) {
  var el = $("#mToast");
  if (!el) {
    el = document.createElement("div");
    el.id = "mToast";
    el.className = "m-toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "m-toast show" + (kind ? " m-toast-" + kind : "");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.className = "m-toast"; }, 2800);
}

function modal(opts) {
  // opts: {title, body(html), bodyText, buttons:[{label, kind, onClick(close)}]}
  var mask = document.createElement("div");
  mask.className = "m-modal-mask";
  var box = document.createElement("div");
  box.className = "m-modal";
  var html = "";
  if (opts.title) html += '<div class="m-modal-title">' + esc(opts.title) + "</div>";
  if (opts.body) html += '<div class="m-modal-body">' + esc(opts.body) + "</div>";
  else if (opts.bodyHtml) html += '<div class="m-modal-body">' + opts.bodyHtml + "</div>";
  mask.appendChild(box);
  document.body.appendChild(mask);

  function close() {
    if (mask.parentNode) mask.parentNode.removeChild(mask);
  }
  var foot = document.createElement("div");
  foot.className = "m-modal-foot";
  (opts.buttons || [{ label: "知道了", kind: "primary" }]).forEach(function (b) {
    var btn = document.createElement("button");
    btn.className = "m-btn m-btn-" + (b.kind || "primary");
    btn.textContent = b.label;
    btn.addEventListener("click", function () {
      if (b.onClick) b.onClick(close);
      else close();
    });
    foot.appendChild(btn);
  });
  box.appendChild(foot);
  return { mask: mask, box: box, close: close };
}

/* 手动输入编码弹窗（扫码回退 / 扫码枪场景） */
function promptCode(title, placeholder, onCode) {
  var m = modal({
    title: title || "输入编码",
    buttons: [
      { label: "取消", kind: "ghost" },
      { label: "确定", kind: "primary", onClick: function (close) {
          var inp = $(".m-modal input.m-code-inp");
          var v = inp ? inp.value.trim() : "";
          if (!v) { toast("请输入编码", "err"); return; }
          close();
          onCode(v);
        } }
    ]
  });
  var bodyDiv = $(".m-modal-body", m.box);
  var inp2 = document.createElement("input");
  inp2.className = "m-input m-code-inp";
  inp2.placeholder = placeholder || "扫码枪扫描或手动输入";
  if (bodyDiv) bodyDiv.appendChild(inp2);
  setTimeout(function () { inp2.focus(); }, 80);
  return m;
}

/* 相机扫码：优先 BarcodeDetector；不可用则回退手动输入 */
function scanCode(onCode) {
  var insecure = !window.isSecureContext;
  var canCamera = !!(window.BarcodeDetector && navigator.mediaDevices &&
    navigator.mediaDevices.getUserMedia) &&
    (!insecure || location.hostname === "localhost" || location.hostname === "127.0.0.1");
  if (!canCamera) {
    promptCode("扫码", "扫码枪扫描或手动输入", onCode);
    return;
  }
  var mask = document.createElement("div");
  mask.className = "m-modal-mask";
  var box = document.createElement("div");
  box.className = "m-modal";
  box.innerHTML =
    '<div class="m-modal-title">扫码</div>' +
    '<div class="m-scan-video-wrap"><video id="mScanVideo" playsinline muted></video></div>' +
    '<div class="m-scan-tip">将条码/二维码对准镜头，识别后自动填入</div>';
  var foot = document.createElement("div");
  foot.className = "m-modal-foot";
  var btnCancel = document.createElement("button");
  btnCancel.className = "m-btn m-btn-ghost";
  btnCancel.textContent = "取消";
  foot.appendChild(btnCancel);
  box.appendChild(foot);
  mask.appendChild(box);
  document.body.appendChild(mask);

  var video = $("#mScanVideo", box);
  var stream = null, timer = null, stopped = false;

  function stop() {
    if (stopped) return;
    stopped = true;
    if (timer) clearInterval(timer);
    if (stream) stream.getTracks().forEach(function (tr) { tr.stop(); });
    if (video && video.srcObject) video.srcObject = null;
    if (mask.parentNode) mask.parentNode.removeChild(mask);
  }
  btnCancel.addEventListener("click", stop);

  navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
    .then(function (s) {
      stream = s;
      video.srcObject = s;
      return video.play();
    })
    .then(function () {
      var detector = new window.BarcodeDetector({
        formats: ["ean_13", "ean_8", "upc_a", "code_128", "code_39", "qr_code", "data_matrix"]
      });
      timer = setInterval(function () {
        if (stopped || !video.videoWidth) return;
        detector.detect(video).then(function (codes) {
          if (stopped || !codes || !codes.length) return;
          var code = codes[0].rawValue || "";
          if (!code) return;
          stop();
          onCode(code);
        }).catch(function () { /* 单帧失败忽略，继续尝试 */ });
      }, 400);
    })
    .catch(function () {
      stop();
      promptCode("扫码（相机不可用）", "扫码枪扫描或手动输入", onCode);
    });
}

/* ---------------- 页面 UI 骨架 ---------------- */
var TABS = [
  { id: "home", href: "index.html", icon: "fa-home", label: "首页" },
  { id: "stock", href: "stock.html", icon: "fa-cubes", label: "库存" },
  { id: "scan", href: "scan.html", icon: "fa-barcode", label: "扫码" },
  { id: "orders", href: "orders.html", icon: "fa-list-alt", label: "单据" },
  { id: "me", href: "profile.html", icon: "fa-user", label: "我的" }
];

function renderChrome(activeTab) {
  var tabEl = $("#mTabbar");
  if (tabEl) {
    var inner = "";
    TABS.forEach(function (t) {
      inner += '<a href="' + t.href + '" class="' + (t.id === activeTab ? "on" : "") + '">' +
        '<i class="fa ' + t.icon + '"></i><span>' + t.label + "</span></a>";
    });
    tabEl.innerHTML = '<div class="m-tabbar-inner">' + inner + "</div>";
  }
  var ctx = $("#mCtx");
  if (ctx && AUTH.isLoggedIn()) {
    var u = AUTH.getUser() || {};
    var w = AUTH.currentWarehouse();
    ctx.innerHTML = '<i class="fa fa-building-o"></i><span>' +
      esc(w ? (w.name || "") : (u.current_warehouse_name || "未选仓库")) + "</span>";
  }
  var back = $("#mBack");
  if (back) {
    back.addEventListener("click", function () {
      if (window.history.length > 1) window.history.back();
      else window.location.href = "index.html";
    });
  }
}

/* ---------------- 全局事件委托 ---------------- */
document.addEventListener("click", function (e) {
  var t = e.target;
  if (!t || !t.closest) return;
  var actEl = t.closest("[data-act]");
  if (actEl) {
    var act = actEl.getAttribute("data-act");
    var arg = actEl.getAttribute("data-arg") || "";
    var fn = (window.M_ACTIONS || {})[act];
    if (fn) {
      e.preventDefault();
      try { fn(actEl, arg); } catch (err) { toast(err.message || "操作失败", "err"); }
    }
    return;
  }
  var scanEl = t.closest("[data-scan]");
  if (scanEl) {
    e.preventDefault();
    var handler = (window.M_ACTIONS || {}).scanFill;
    if (handler) {
      scanCode(function (code) {
        try { handler(code, scanEl); } catch (err) { toast(err.message || "操作失败", "err"); }
      });
    }
  }
});

/* ---------------- 状态徽章 ---------------- */
function statusBadge(status) {
  var map = {
    DRAFT: ["草稿", "m-badge-draft"],
    IN_PROGRESS: ["盘点中", "m-badge-prog"],
    COMPLETED: ["已完成", "m-badge-done"],
    PENDING: ["待处理", "m-badge-warn"],
    APPROVED: ["已通过", "m-badge-done"],
    REJECTED: ["已驳回", "m-badge-out"]
  };
  var m = map[status] || [status || "未知", "m-badge-draft"];
  return '<span class="m-badge ' + m[1] + '">' + esc(m[0]) + "</span>";
}
function typeBadge(type) {
  if (type === "入库" || type === "IN") return '<span class="m-badge m-badge-in">入库</span>';
  if (type === "出库" || type === "OUT") return '<span class="m-badge m-badge-out">出库</span>';
  return '<span class="m-badge">' + esc(type || "") + "</span>";
}

/* ---------------- 对外 API ---------------- */
window.M = {
  API_BASE: API_BASE,
  esc: esc,
  fmtNum: fmtNum,
  fmtDT: fmtDT,
  api: api,
  apiForm: apiForm,
  AUTH: AUTH,
  toast: toast,
  modal: modal,
  promptCode: promptCode,
  renderChrome: renderChrome,
  statusBadge: statusBadge,
  typeBadge: typeBadge,
  scanCode: scanCode,
  $: $,
  $all: $all
};

/* 页面脚本约定：
 *   window.M_PAGES = window.M_PAGES || {};
 *   window.M_PAGES["stock"] = function(){ ... };   // 在 mobile.js 之前加载
 * 启动：window load 后执行（确保所有页面脚本已注册） */
function boot() {
  var page = document.body.getAttribute("data-page") || "index";
  var activeTab = { index: "home", stock: "stock", scan: "scan", orders: "orders", profile: "me" }[page] || "";
  renderChrome(activeTab);
  var init = (window.M_PAGES || {})[page];
  if (init) {
    try { init(); } catch (e) { toast(e.message || "页面初始化失败", "err"); }
  }
}
window.addEventListener("load", boot);
})();

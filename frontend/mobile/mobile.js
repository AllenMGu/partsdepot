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

/* ---------------- 鉴权（与桌面端 common.js 共享键名，读写语义对齐桌面） ----------------
 * 桌面端 getStoredAuth() 的语义：local/session 各作为一个"完整 auth pair
 * （user + token_expiry）"，按"local 有效 > session 有效 > local（即使过期）
 * > session（即使过期）"选最佳有效项——绝不跨 storage 拼 user/expiry。
 * H5 旧实现 getUser/getExpiry 各自独立"localStorage 优先"，当 local 残留旧用户、
 * session 是当前有效用户时会发生 user/expiry 配对错误，现改为与桌面完全一致。
 * 另外记住当前 auth 来源（local/session）：save() 未显式指定目标时写回原
 * storage——修复"session-only 用户（桌面不勾记住我）在 H5 切仓后被转写进
 * localStorage"的迁移问题。
 * 移动端登录页无"记住我"开关：新登录显式写 local（等同桌面勾选记住我）。 */
var AUTH = {
  _source: null, // "local" | "session" | null —— 当前 auth 对的来源（每页加载重新计算）
  _pair: function (storage) {
    return { user: storage.getItem("user"), expiry: storage.getItem("token_expiry") || "" };
  },
  _pick: function (localPair, sessionPair) {
    var complete = function (p) { return !!p.user; };
    var expired = function (p) { return !!(p.expiry && Date.now() >= new Date(p.expiry).getTime()); };
    if (complete(localPair) && !expired(localPair)) return { pair: localPair, src: "local" };
    if (complete(sessionPair) && !expired(sessionPair)) return { pair: sessionPair, src: "session" };
    if (complete(localPair)) return { pair: localPair, src: "local" };
    if (complete(sessionPair)) return { pair: sessionPair, src: "session" };
    return { pair: { user: null, expiry: "" }, src: null };
  },
  getAuth: function () {
    try {
      var best = this._pick(this._pair(localStorage), this._pair(sessionStorage));
      this._source = best.src;
      var user = null;
      if (best.pair.user) {
        try { user = JSON.parse(best.pair.user); } catch (e) { user = null; }
      }
      return { user: user, expiry: best.pair.expiry || "", src: best.src };
    } catch (e) {
      return { user: null, expiry: "", src: null };
    }
  },
  getUser: function () { return this.getAuth().user; },
  getExpiry: function () { return this.getAuth().expiry; },
  isLoggedIn: function () {
    var a = this.getAuth();
    if (!a.user) return false;
    return !a.expiry || Date.now() < new Date(a.expiry).getTime();
  },
  save: function (user, expiry, target) {
    // target: "local" | "session"（登录等显式场景）；
    // 未指定时写回当前 auth 来源；无既有 auth（全新登录）默认 local
    var st;
    if (target === "session") st = sessionStorage;
    else if (target === "local") st = localStorage;
    else st = (this._source === "session") ? sessionStorage : localStorage;
    if (user) st.setItem("user", JSON.stringify(user));
    if (expiry) st.setItem("token_expiry", String(expiry));
  },
  clear: function () {
    this._source = null;
    localStorage.removeItem("user");
    localStorage.removeItem("token_expiry");
    sessionStorage.removeItem("user");
    sessionStorage.removeItem("token_expiry");
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
    // 注意：后端 current_warehouse_id 为空时的 list[0] 只是过渡展示位，
    // 真正的"当前仓库"建立必须走 ensureCurrentWarehouse() 同步到服务端。
    return u.current_warehouse_name ? { id: id, name: u.current_warehouse_name } : (list[0] || null);
  },
  /* 首次使用场景：用户有仓库、但后端 current_warehouse_id 为空。
   * 纯前端 fallback（把 warehouses[0] 当"当前仓库"）会导致：页面/我的页把该仓
   * 显示为"当前"，而入库/出库创建依赖服务端真实的 current_warehouse_id →
   * 后端 400"请先选择当前仓库"；单仓库用户甚至没有"切换"按钮可自救。
   * 因此这里自动选择（is_default 优先，否则 warehouses[0]）并真实调用
   * POST /users/{id}/switch-warehouse 同步到服务端，成功后更新 AUTH 快照。
   * 失败仅提示、不阻塞页面初始化（返回的 Promise 总是 resolve）。 */
  ensureCurrentWarehouse: function () {
    var self = this;
    var a = this.getAuth();
    var u = a.user;
    if (!u || !u.id || u.current_warehouse_id) return Promise.resolve();
    var list = u.warehouses || [];
    if (!list.length) return Promise.resolve();
    var target = list[0];
    for (var i = 0; i < list.length; i++) if (list[i].is_default) { target = list[i]; break; }
    return api("POST", "/users/" + u.id + "/switch-warehouse?warehouse_id=" + target.id)
      .then(function (res) {
        var nu = Object.assign({}, u, {
          current_warehouse_id: (res && res.current_warehouse_id) || target.id,
          current_warehouse_name: (res && res.current_warehouse_name) || target.name
        });
        self.save(nu, a.expiry); // 写回原 storage（local/session）
        return nu;
      })
      .catch(function (err) {
        toast("自动选择当前仓库失败：" + ((err && err.message) || err), "err");
      });
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
  Object.keys(opts.headers || {}).forEach(function (k) { headers[k] = opts.headers[k]; });
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
  // opts: {title, body(纯文本), bodyHtml(受信 HTML), buttons:[{label, kind, onClick(close)}]}
  var mask = document.createElement("div");
  mask.className = "m-modal-mask";
  var box = document.createElement("div");
  box.className = "m-modal";

  function close() {
    if (mask.parentNode) mask.parentNode.removeChild(mask);
  }

  // 标题（textContent，杜绝注入）
  if (opts.title) {
    var t = document.createElement("div");
    t.className = "m-modal-title";
    t.textContent = opts.title;
    box.appendChild(t);
  }
  // 正文：始终创建 .m-modal-body（promptCode 等依赖此节点挂输入框）
  var bodyDiv = document.createElement("div");
  bodyDiv.className = "m-modal-body";
  if (opts.body != null) bodyDiv.textContent = opts.body;
  else if (opts.bodyHtml) bodyDiv.innerHTML = opts.bodyHtml; // 仅允许页面代码自身拼接的受信 HTML
  box.appendChild(bodyDiv);
  // 按钮
  var foot = document.createElement("div");
  foot.className = "m-modal-foot";
  (opts.buttons || [{ label: "知道了", kind: "primary" }]).forEach(function (b) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "m-btn m-btn-" + (b.kind || "primary");
    btn.textContent = b.label;
    btn.addEventListener("click", function () {
      if (b.onClick) b.onClick(close);
      else close();
    });
    foot.appendChild(btn);
  });
  box.appendChild(foot);

  mask.appendChild(box);
  document.body.appendChild(mask);
  return { mask: mask, box: box, body: bodyDiv, close: close };
}

/* 手动输入编码弹窗（扫码回退 / 扫码枪场景） */
function promptCode(title, placeholder, onCode, onCancel) {
  var inp = document.createElement("input");
  inp.type = "text";
  inp.autocomplete = "off";
  inp.className = "m-input m-code-inp";
  inp.placeholder = placeholder || "扫码枪扫描或手动输入";
  var m = modal({
    title: title || "输入编码",
    buttons: [
      { label: "取消", kind: "ghost", onClick: function (close) {
          if (onCancel) onCancel();
          close();
        } },
      { label: "确定", kind: "primary", onClick: function (close) {
          var v = inp.value.trim();
          if (!v) { toast("请输入编码", "err"); inp.focus(); return; }
          close();
          onCode(v);
        } }
    ]
  });
  m.body.appendChild(inp);
  setTimeout(function () { inp.focus(); inp.select && inp.select(); }, 80);
  return m;
}

/* 相机扫码：优先 BarcodeDetector；不可用则回退手动输入 */
function scanCode(onCode, options) {
  options = options || {};
  var continuous = !!options.continuous;
  var cancelled = false;
  var fallbackModal = null;
  var stream = null, timer = null, mask = null, video = null;

  function cleanupCamera() {
    if (timer) { clearInterval(timer); timer = null; }
    if (stream) { stream.getTracks().forEach(function (tr) { tr.stop(); }); stream = null; }
    if (video && video.srcObject) video.srcObject = null;
    if (mask && mask.parentNode) mask.parentNode.removeChild(mask);
    mask = null;
    video = null;
  }

  function cancel() {
    if (cancelled) return;
    cancelled = true;
    stopped = true;
    cleanupCamera();
    if (fallbackModal) { fallbackModal.close(); fallbackModal = null; }
    if (options.onCancel) options.onCancel();
  }

  function openManualInput() {
    if (cancelled) return;
    fallbackModal = promptCode(
      "摄像头不可用，请手工输入条码",
      "扫码枪扫描或手动输入",
      function (code) {
        fallbackModal = null;
        if (cancelled) return;
        function deliver() {
          if (cancelled) return;
          // 连续扫码页面可能正在等待上一个条码的网络校验；保留本次输入，
          // 待消费者解除 busy 后再交付，避免手工扫码枪输入被吞掉。
          var accepted = onCode(code);
          if (continuous && accepted === false) return setTimeout(deliver, 120);
          if (continuous && !cancelled) setTimeout(openManualInput, 0);
        }
        deliver();
      },
      cancel
    );
  }

  var insecure = !window.isSecureContext;
  var canCamera = !!(window.BarcodeDetector && navigator.mediaDevices &&
    navigator.mediaDevices.getUserMedia) &&
    (!insecure || location.hostname === "localhost" || location.hostname === "127.0.0.1");
  if (!canCamera) {
    openManualInput();
    return { cancel: cancel };
  }
  mask = document.createElement("div");
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

  video = $("#mScanVideo", box);
  var stopped = false;
  var blockedCode = null;
  var blockedMisses = 0;

  function stopOneShot() {
    if (stopped) return;
    stopped = true;
    cleanupCamera();
  }
  btnCancel.addEventListener("click", cancel);

  navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
    .then(function (s) {
      if (cancelled || stopped) {
        s.getTracks().forEach(function (tr) { tr.stop(); });
        return Promise.reject(new Error("scan cancelled"));
      }
      stream = s;
      video.srcObject = s;
      return video.play();
    })
    .then(function () {
      if (cancelled || stopped) return;
      var detector = new window.BarcodeDetector({
        formats: ["ean_13", "ean_8", "upc_a", "code_128", "code_39", "qr_code", "data_matrix"]
      });
      timer = setInterval(function () {
        if (stopped || !video.videoWidth) return;
        detector.detect(video).then(function (codes) {
          if (stopped) return;
          if (!codes || !codes.length) {
            if (continuous && blockedCode && ++blockedMisses >= 2) {
              blockedCode = null;
              blockedMisses = 0;
            }
            return;
          }
          var code = codes[0].rawValue || "";
          if (!code) return;
          if (continuous) {
            if (blockedCode === code) {
              blockedMisses = 0;
              return;
            }
            // 回调返回 false 表示业务仍在处理，保持相机中的条码未消费，
            // 避免第二件货物在网络校验期间被吞掉。
            if (onCode(code) !== false) {
              blockedCode = code;
              blockedMisses = 0;
            }
            return;
          }
          stopOneShot();
          onCode(code);
        }).catch(function () { /* 单帧失败忽略，继续尝试 */ });
      }, 400);
    })
    .catch(function () {
      stopOneShot();
      openManualInput();
    });
  return { cancel: cancel };
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
      // 必须在用户点击事件内创建/恢复 AudioContext，避免普通单次扫码完成后才初始化而被浏览器拦截。
      if (window.M && window.M.prepareScanAudio) window.M.prepareScanAudio();
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
  if (!init) return;
  // 首次使用：已登录、有仓库、但后端 current_warehouse_id 为空 →
  // 先自动建立当前仓库（真实同步到服务端），再初始化页面；
  // 否则页面会把 warehouses[0] 显示为"当前仓库"，而创建单据时后端 400。
  var u0 = AUTH.isLoggedIn() ? AUTH.getUser() : null;
  var ready = (u0 && !u0.current_warehouse_id && (u0.warehouses || []).length)
      ? AUTH.ensureCurrentWarehouse().then(function () { renderChrome(activeTab); })
      : Promise.resolve();
  ready.then(function () {
    try { init(); } catch (e) { toast(e.message || "页面初始化失败", "err"); }
  });
}
window.addEventListener("load", boot);
})();

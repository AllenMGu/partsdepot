/* 首页（对标小程序 pages/home）：概览 + 功能入口，匿名可用 */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["index"] = function () {
  var M = window.M;
  var loggedIn = M.AUTH.isLoggedIn();
  document.getElementById("mHomeLoggedIn").classList.toggle("m-hidden", !loggedIn);
  document.getElementById("mHomeAnon").classList.toggle("m-hidden", loggedIn);
  document.getElementById("mHomeGrid").classList.toggle("m-hidden", !loggedIn);

  if (loggedIn) {
    var u = M.AUTH.getUser() || {};
    var nameEl = document.getElementById("mHomeUserName");
    if (nameEl) nameEl.textContent = u.full_name || u.username || "";
    loadOverview();
  }

  function loadOverview() {
    var box = document.getElementById("mOverview");
    box.innerHTML = '<div class="m-empty">加载中…</div>';
    // 按当前仓库查询（后端 /stock/ 对管理员返回全部仓库，前端不能拿全量再猜）
    var w = M.AUTH.currentWarehouse();
    var wh = (w && w.id) ? "?warehouse_id=" + w.id : "";
    // 注意：/inventory/logs 自带 ?limit=10，追加参数必须用 &
    var wh2 = (w && w.id) ? "&warehouse_id=" + w.id : "";
    Promise.all([
      M.api("GET", "/stock/" + wh, null, { auth: true }),
      M.api("GET", "/inventory/logs?limit=10" + wh2, null, { auth: true })
    ]).then(function (res) {
      var stocks = res[0] || [];
      var logs = res[1] || [];
      var total = stocks.reduce(function (s, it) { return s + Number(it.quantity || 0); }, 0);
      box.innerHTML =
        '<div class="m-stats">' +
        '<div class="m-stat"><div class="m-stat-num">' + stocks.length + '</div><div class="m-stat-label">库存条目</div></div>' +
        '<div class="m-stat"><div class="m-stat-num">' + M.fmtNum(total) + '</div><div class="m-stat-label">总数量</div></div>' +
        '<div class="m-stat"><div class="m-stat-num">' + logs.length + '</div><div class="m-stat-label">近期流水</div></div>' +
        "</div>";
    }).catch(function (err) {
      box.innerHTML = '<div class="m-empty">' + M.esc(err.message || "概览加载失败") + "</div>";
    });
  }

  window.M_ACTIONS["goApply"] = function () { window.location.href = "apply.html"; };
  window.M_ACTIONS["goScan"] = function () { window.location.href = "scan.html"; };
  window.M_ACTIONS["goOrders"] = function () { window.location.href = "orders.html"; };
  window.M_ACTIONS["goLogin"] = function () { window.location.href = "login.html"; };
  window.M_ACTIONS["logout"] = function () {
    M.modal({
      title: "退出登录",
      body: "确认退出当前账号？",
      buttons: [
        { label: "取消", kind: "ghost" },
        { label: "退出", kind: "danger", onClick: function (close) {
            close();
            (function () {
              var done = function () {
                M.AUTH.clear();
                window.location.href = "login.html";
              };
              M.api("POST", "/logout").then(done).catch(done);
            })();
        } }
      ]
    });
  };
};

/* 我的页面（对标小程序 pages/profile）：用户信息 / 切换仓库 / 退出登录 */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["profile"] = function () {
  var M = window.M;
  if (!M.AUTH.require()) return;

  var elUser = document.getElementById("mProfileUser");
  var elWhList = document.getElementById("mProfileWarehouses");

  render();

  function render() {
    var u = M.AUTH.getUser() || {};
    var w = M.AUTH.currentWarehouse();
    var roleText = { admin: "管理员", operator: "操作员" }[u.role] || (u.role || "");
    elUser.innerHTML =
      '<div class="m-row" style="gap:12px;align-items:center;">' +
      '<div style="width:48px;height:48px;border-radius:50%;background:var(--m-primary);color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;">' +
      M.esc(((u.full_name || u.username || "U").charAt(0)).toUpperCase()) + "</div>" +
      '<div class="m-grow"><div class="m-item-title">' + M.esc(u.full_name || u.username) + "</div>" +
      '<div class="m-item-sub">' + M.esc(roleText) +
      (w ? " · 当前仓库：" + M.esc(w.name) : "") + "</div></div>" +
      "</div>";

    var list = u.warehouses || [];
    if (!list.length) {
      elWhList.innerHTML = '<div class="m-hint">未分配仓库</div>';
      return;
    }
    elWhList.innerHTML = list.map(function (wh) {
      var on = w && w.id === wh.id;
      return '<div class="m-item" style="border-bottom:1px solid var(--m-line);padding:10px 0;">' +
        '<div class="m-row between">' +
        "<div><div style=\"font-size:14px;font-weight:500;\">" + M.esc(wh.name) + "</div>" +
        '<div class="m-item-sub m-mono">' + M.esc(wh.code || "") + "</div></div>" +
        (on ? '<span class="m-badge m-badge-done">当前</span>'
            : '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-act="whSwitch" data-arg="' + wh.id + '">切换</button>') +
        "</div>" +
        "</div>";
    }).join("");
  }

  window.M_ACTIONS["whSwitch"] = function (el, arg) {
    var id = Number(arg);
    var u = M.AUTH.getUser() || {};
    if (!id || !u.id) return;
    M.toast("切换中…");
    M.api("POST", "/users/" + u.id + "/switch-warehouse?warehouse_id=" + id)
      .then(function () {
        var name = null;
        (u.warehouses || []).forEach(function (wh) { if (wh.id === id) name = wh.name; });
        var newUser = Object.assign({}, u, {
          current_warehouse_id: id,
          current_warehouse_name: name || u.current_warehouse_name
        });
        M.AUTH.save(newUser, M.AUTH.getExpiry());
        render();
        M.renderChrome("me");
        M.toast("切换成功", "ok");
      })
      .catch(function (err) { M.toast(err.message || "切换失败", "err"); });
  };

  window.M_ACTIONS["logout"] = function () {
    M.modal({
      title: "退出登录",
      body: "确认退出当前账号？",
      buttons: [
        { label: "取消", kind: "ghost" },
        { label: "退出", kind: "danger", onClick: function (close) {
            close();
            var done = function () {
              M.AUTH.clear();
              window.location.href = "login.html";
            };
            M.api("POST", "/logout").then(done).catch(done);
        } }
      ]
    });
  };
};

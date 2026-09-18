/* 登录页（对标小程序 pages/login） */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["login"] = function () {
  var form = document.getElementById("mLoginForm");
  if (!form) return;
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    doLogin();
  });

  function doLogin() {
    var username = (document.getElementById("mLoginUser").value || "").trim();
    var password = document.getElementById("mLoginPass").value || "";
    if (!username || !password) {
      window.M.toast("请输入用户名和密码", "err");
      return;
    }
    var btn = document.getElementById("mLoginBtn");
    btn.disabled = true;
    btn.textContent = "登录中…";
    window.M.apiForm("POST", "/token", { username: username, password: password })
      .then(function (res) {
        window.M.AUTH.save(res.user, res.expiry);
        window.M.toast("登录成功", "ok");
        setTimeout(function () { window.location.href = "index.html"; }, 350);
      })
      .catch(function (err) {
        window.M.toast(err.message || "登录失败", "err");
        btn.disabled = false;
        btn.textContent = "登 录";
      });
  }

  // 未登录申请入口
  window.M_ACTIONS["goApply"] = function () { window.location.href = "apply.html"; };
};

const { getToken, getUser, isExpired } = require("./auth");

function requireLogin() {
  const token = getToken();
  if (!token || isExpired()) {
    wx.reLaunch({ url: "/pages/login/index" });
    return false;
  }
  return true;
}

// 只判断登录态，不触发跳转（用于首页等匿名可用的页面）
function isLoggedIn() {
  const token = getToken();
  return Boolean(token && !isExpired());
}

function requireOperator() {
  if (!requireLogin()) return false;
  if (["admin", "supervisor", "operator"].includes((getUser() || {}).role)) return true;
  wx.showToast({ title: "当前账号没有仓库操作权限", icon: "none" });
  wx.switchTab({ url: "/pages/stock/index" });
  return false;
}

module.exports = { requireLogin, requireOperator, isLoggedIn };

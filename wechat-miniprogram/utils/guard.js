const { getToken, isExpired } = require("./auth");

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

module.exports = { requireLogin, isLoggedIn };

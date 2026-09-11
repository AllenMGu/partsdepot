const { getToken, isExpired } = require("./auth");

function requireLogin() {
  const token = getToken();
  if (!token || isExpired()) {
    wx.reLaunch({ url: "/pages/login/index" });
    return false;
  }
  return true;
}

module.exports = { requireLogin };

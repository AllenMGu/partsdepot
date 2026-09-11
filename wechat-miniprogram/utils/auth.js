const TOKEN_KEY = "wms_access_token";
const USER_KEY = "wms_user";
const EXPIRY_KEY = "wms_token_expiry";

function saveAuth(payload) {
  let expiry = payload.expiry || "";
  if (!expiry) {
    const ms30Days = 30 * 24 * 60 * 60 * 1000;
    expiry = new Date(Date.now() + ms30Days).toISOString();
  }
  wx.setStorageSync(TOKEN_KEY, payload.access_token || "");
  wx.setStorageSync(USER_KEY, payload.user || null);
  wx.setStorageSync(EXPIRY_KEY, expiry);
}

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function getUser() {
  return wx.getStorageSync(USER_KEY) || null;
}

function getExpiry() {
  return wx.getStorageSync(EXPIRY_KEY) || "";
}

function clearAuth() {
  wx.removeStorageSync(TOKEN_KEY);
  wx.removeStorageSync(USER_KEY);
  wx.removeStorageSync(EXPIRY_KEY);
}

function isExpired() {
  const expiry = getExpiry();
  if (!expiry) return false;
  return new Date().getTime() >= new Date(expiry).getTime();
}

module.exports = {
  saveAuth,
  getToken,
  getUser,
  getExpiry,
  clearAuth,
  isExpired
};

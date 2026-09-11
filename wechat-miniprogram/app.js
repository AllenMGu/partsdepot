const { getToken, getUser, clearAuth } = require("./utils/auth");

App({
  globalData: {
    apiBaseUrl: "HTTPS://API HOST ",
    token: "",
    user: null
  },
  onLaunch() {
    this.globalData.token = getToken() || "";
    this.globalData.user = getUser() || null;
  },
  logoutAndGoLogin() {
    clearAuth();
    this.globalData.token = "";
    this.globalData.user = null;
    wx.reLaunch({ url: "/pages/login/index" });
  }
});

const { getToken, getUser, clearAuth } = require("./utils/auth");

App({
  globalData: {
    // 生产环境 API 地址（需为已在微信小程序后台配置的 request 合法域名，HTTPS）
    apiBaseUrl: "https://cuwxwms01.cutiatx.com/api",
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

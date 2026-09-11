const { login } = require("../../utils/api");
const { saveAuth, getToken, getUser, isExpired } = require("../../utils/auth");

Page({
  data: {
    username: "",
    password: "",
    loading: false
  },
  onShow() {
    const token = getToken();
    const user = getUser();
    if (token && user && !isExpired()) {
      const app = getApp();
      app.globalData.token = token;
      app.globalData.user = user;
      wx.switchTab({ url: "/pages/home/index" });
      return;
    }
  },
  onUsernameInput(e) {
    this.setData({ username: e.detail.value.trim() });
  },
  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },
  async handleLogin() {
    const { username, password } = this.data;
    if (!username || !password) {
      wx.showToast({ title: "请输入用户名和密码", icon: "none" });
      return;
    }

    this.setData({ loading: true });
    try {
      const payload = await login(username, password);
      saveAuth(payload);

      const app = getApp();
      app.globalData.token = payload.access_token;
      app.globalData.user = payload.user;

      wx.showToast({ title: "登录成功", icon: "success" });
      wx.switchTab({ url: "/pages/home/index" });
    } catch (err) {
      wx.showToast({ title: err.message || "登录失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  }
});

const { request } = require("../../utils/api");
const { getUser, clearAuth } = require("../../utils/auth");
const { requireLogin } = require("../../utils/guard");
const { fmtNum } = require("../../utils/format");

Page({
  data: {
    user: {},
    stockCount: 0,
    totalQty: "0.00",
    logsCount: 0
  },
  async onShow() {
    if (!requireLogin()) return;
    this.setData({ user: getUser() || {} });
    await this.loadOverview();
  },
  async loadOverview() {
    wx.showLoading({ title: "加载中" });
    try {
      const [stocks, logs] = await Promise.all([
        request({ url: "/stock/" }),
        request({ url: "/inventory/logs", data: { limit: 10 } })
      ]);
      const total = (stocks || []).reduce((sum, item) => sum + Number(item.quantity || 0), 0);
      this.setData({
        stockCount: (stocks || []).length,
        totalQty: fmtNum(total),
        logsCount: (logs || []).length
      });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
  goScan() {
    wx.switchTab({ url: "/pages/scan/index" });
  },
  goOrders() {
    wx.navigateTo({ url: "/pages/orders/index" });
  },
  goStock() {
    wx.switchTab({ url: "/pages/stock/index" });
  },
  logout() {
    clearAuth();
    getApp().globalData.token = "";
    getApp().globalData.user = null;
    wx.reLaunch({ url: "/pages/login/index" });
  }
});

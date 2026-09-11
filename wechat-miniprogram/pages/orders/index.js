const { requireLogin } = require("../../utils/guard");

Page({
  onShow() {
    requireLogin();
  },
  goInbound() {
    wx.navigateTo({ url: "/pages/inbound/index" });
  },
  goOutbound() {
    wx.navigateTo({ url: "/pages/outbound/index" });
  },
  goCheck() {
    wx.navigateTo({ url: "/pages/check-order/index" });
  }
});

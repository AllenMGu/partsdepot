const { requireOperator } = require("../../utils/guard");

Page({
  onShow() {
    requireOperator();
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

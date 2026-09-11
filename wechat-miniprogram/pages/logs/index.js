const { request } = require("../../utils/api");
const { requireLogin } = require("../../utils/guard");
const { fmtDateTime, fmtNum } = require("../../utils/format");

Page({
  data: {
    list: [],
    limit: 50
  },
  async onShow() {
    if (!requireLogin()) return;
    await this.loadLogs();
  },
  async loadLogs() {
    wx.showLoading({ title: "加载中" });
    try {
      const res = await request({ url: "/inventory/logs", data: { limit: this.data.limit } });
      const list = (res || []).map((item) => ({
        ...item,
        quantity: fmtNum(item.quantity),
        create_time_fmt: fmtDateTime(item.create_time)
      }));
      this.setData({ list });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  }
});

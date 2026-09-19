const { request } = require("../../utils/api");
const { getUser } = require("../../utils/auth");
const { requireLogin } = require("../../utils/guard");
const { fmtDateTime, fmtNum } = require("../../utils/format");

Page({
  data: {
    currentWarehouseName: "",
    rawList: [],
    list: [],
    keyword: ""
  },
  async onShow() {
    if (!requireLogin()) return;
    const user = getUser() || {};
    this.setData({ currentWarehouseName: user.current_warehouse_name || "" });
    await this.loadStock();
  },
  async loadStock() {
    wx.showLoading({ title: "加载中" });
    try {
      const user = getUser() || {};
      const res = await request({ url: "/stock/", data: { warehouse_id: user.current_warehouse_id || undefined } });
      const filtered = res || [];
      const normalized = filtered.map((item) => ({
        ...item,
        quantity: fmtNum(item.quantity),
        update_time_fmt: fmtDateTime(item.update_time)
      }));
      this.setData({ rawList: normalized }, () => this.applyFilter());
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value.trim() }, () => this.applyFilter());
  },
  scanStockCode() {
    wx.scanCode({
      onlyFromCamera: false,
      success: async (res) => {
        const code = String(res.result || "").trim();
        if (!code) return wx.showToast({ title: "未读取到条码", icon: "none" });
        if (wx.vibrateShort) wx.vibrateShort({ type: "light" });
        this.setData({ keyword: code });
        await this.loadStock();
      },
      fail: () => wx.showToast({ title: "扫码取消/失败，请手工输入", icon: "none" })
    });
  },
  applyFilter() {
    const kw = (this.data.keyword || "").toLowerCase();
    if (!kw) {
      this.setData({ list: this.data.rawList });
      return;
    }
    const list = this.data.rawList.filter((item) =>
      [item.goods_barcode, item.goods_name, item.location_code, item.warehouse_name]
        .join("|")
        .toLowerCase()
        .includes(kw)
    );
    this.setData({ list });
  }
});

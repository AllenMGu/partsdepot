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
      const res = await request({ url: "/stock/" });
      const warehouseName = this.data.currentWarehouseName;
      const filtered = (res || []).filter((item) =>
        warehouseName ? item.warehouse_name === warehouseName : true
      );
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

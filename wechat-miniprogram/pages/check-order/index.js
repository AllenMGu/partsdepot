const { request } = require("../../utils/api");
const { requireLogin } = require("../../utils/guard");
const { fmtDateTime, fmtNum } = require("../../utils/format");

function normalizeItem(it) {
  const diff = Number(it.diff_quantity || 0);
  return {
    ...it,
    check_quantity: fmtNum(it.check_quantity),
    actual_quantity: fmtNum(it.actual_quantity),
    diff_quantity: fmtNum(it.diff_quantity),
    diff_class: Math.abs(diff) < 0.01 ? "ok" : "warn"
  };
}

Page({
  data: {
    labels: {
      createHint: "先创建盘点单，再扫码盘点",
      remark: "备注(选填)",
      createOrder: "创建盘点单",
      orderList: "盘点单列表",
      refresh: "刷新",
      warehouse: "仓库：",
      itemCount: "明细：",
      createTime: "创建：",
      process: "扫码盘点",
      currentOrder: "当前盘点单：",
      goodsBarcode: "货物条码",
      locationCode: "库位编码",
      scanGoods: "扫码货物",
      scanLocation: "扫码库位",
      stock: "当前系统库存：",
      checkQty: "盘点数量",
      confirmQty: "确认数量",
      complete: "完成盘点",
      items: "盘点明细",
      noItems: "暂无盘点明细",
      system: "系统：",
      checked: "盘点：",
      diff: "差异："
    },
    orders: [],
    selectedHeader: {},
    selectedItems: [],
    createForm: { remark: "" },
    itemForm: { goods_barcode: "", location_code: "", check_quantity: "" },
    goodsList: [],
    locationList: [],
    goodsMatched: false,
    locationMatched: false,
    goodsInfoText: "",
    locationInfoText: "",
    currentStock: "-",
    loadingCreate: false,
    loadingAddItem: false,
    loadingComplete: false
  },

  async onShow() {
    if (!requireLogin()) return;
    await Promise.all([this.loadOrders(), this.loadGoods()]);
  },

  onCreateRemarkInput(e) { this.setData({ "createForm.remark": e.detail.value }); },
  onItemBarcodeInput(e) { this.setData({ "itemForm.goods_barcode": e.detail.value.trim() }, () => this.validateGoodsAndStock()); },
  onItemLocationInput(e) { this.setData({ "itemForm.location_code": e.detail.value.trim() }, () => this.validateLocationAndStock()); },
  onItemQtyInput(e) { this.setData({ "itemForm.check_quantity": e.detail.value.trim() }); },

  scanGoodsCode() { this.scanCode((code) => this.setData({ "itemForm.goods_barcode": code }, () => this.validateGoodsAndStock())); },
  scanLocationCode() { this.scanCode((code) => this.setData({ "itemForm.location_code": code }, () => this.validateLocationAndStock())); },

  scanCode(onSuccess) {
    wx.scanCode({
      onlyFromCamera: false,
      success: (res) => onSuccess(res.result || ""),
      fail: () => wx.showToast({ title: "扫码取消/失败", icon: "none" })
    });
  },

  async loadGoods() {
    try {
      const list = await request({ url: "/goods/" });
      this.setData({ goodsList: list || [] });
    } catch (err) {
      wx.showToast({ title: err.message || "货物加载失败", icon: "none" });
    }
  },

  async loadLocationsForOrderWarehouse() {
    const warehouseId = this.data.selectedHeader.warehouse_id;
    if (!warehouseId) return this.setData({ locationList: [] });
    try {
      const list = await request({ url: "/locations/", data: { warehouse_id: warehouseId } });
      this.setData({ locationList: list || [] });
    } catch (err) {
      wx.showToast({ title: err.message || "库位加载失败", icon: "none" });
    }
  },

  validateGoodsAndStock() {
    const code = this.data.itemForm.goods_barcode;
    const goods = (this.data.goodsList || []).find((g) => g.barcode === code);
    if (!goods) return this.setData({ goodsMatched: false, goodsInfoText: code ? "货物不存在" : "", currentStock: "-" });
    this.setData({ goodsMatched: true, goodsInfoText: `${goods.name} | ${goods.spec || "无规格"} | ${goods.unit}` });
    this.refreshCurrentStock();
  },

  validateLocationAndStock() {
    const code = this.data.itemForm.location_code;
    const loc = (this.data.locationList || []).find((l) => l.location_code === code);
    if (!loc) return this.setData({ locationMatched: false, locationInfoText: code ? "库位不存在或不在当前盘点仓库" : "", currentStock: "-" });
    this.setData({ locationMatched: true, locationInfoText: `${loc.location_code} | ${loc.name}` });
    this.refreshCurrentStock();
  },

  async refreshCurrentStock() {
    if (!this.data.goodsMatched || !this.data.locationMatched) return this.setData({ currentStock: "-" });
    try {
      const rows = await request({ url: "/stock/" });
      const barcode = this.data.itemForm.goods_barcode;
      const locationCode = this.data.itemForm.location_code;
      const warehouseName = this.data.selectedHeader.warehouse_name;
      const stock = (rows || []).find((s) => s.goods_barcode === barcode && s.location_code === locationCode && (!warehouseName || s.warehouse_name === warehouseName));
      this.setData({ currentStock: stock ? fmtNum(stock.quantity) : "0.00" });
    } catch (_) {
      this.setData({ currentStock: "-" });
    }
  },

  async loadOrders() {
    wx.showLoading({ title: "加载中" });
    try {
      const res = await request({ url: "/check-orders/" });
      const list = ((res && res.data) || []).map((item) => ({ ...item, create_time_fmt: fmtDateTime(item.create_time) }));
      this.setData({ orders: list });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },

  async createOrder() {
    this.setData({ loadingCreate: true });
    try {
      const created = await request({ url: "/check-orders/", method: "POST", data: { remark: this.data.createForm.remark || null } });
      wx.showToast({ title: "盘点单已创建", icon: "success" });
      this.setData({ createForm: { remark: "" } });
      await this.loadOrders();
      await this.openDetailById(created.id);
    } catch (err) {
      wx.showToast({ title: err.message || "创建失败", icon: "none" });
    } finally {
      this.setData({ loadingCreate: false });
    }
  },

  async openDetail(e) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    await this.openDetailById(id);
  },

  async openDetailById(id) {
    wx.showLoading({ title: "加载详情" });
    try {
      const res = await request({ url: `/check-orders/${id}` });
      this.setData({
        selectedHeader: res.header || {},
        selectedItems: normalizeItem ? (res.items || []).map(normalizeItem) : [],
        itemForm: { goods_barcode: "", location_code: "", check_quantity: "" },
        goodsMatched: false,
        locationMatched: false,
        goodsInfoText: "",
        locationInfoText: "",
        currentStock: "-"
      });
      await this.loadLocationsForOrderWarehouse();
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },

  closeDetail() { this.setData({ selectedHeader: {}, selectedItems: [] }); },

  resetCheckForm() {
    this.setData({
      itemForm: { goods_barcode: "", location_code: "", check_quantity: "" },
      goodsMatched: false,
      locationMatched: false,
      goodsInfoText: "",
      locationInfoText: "",
      currentStock: "-"
    });
  },

  async addItem() {
    const headerId = this.data.selectedHeader.id;
    const qty = Number(this.data.itemForm.check_quantity);
    if (!headerId) return wx.showToast({ title: "请先创建或选择盘点单", icon: "none" });
    if (!this.data.itemForm.goods_barcode || !this.data.itemForm.location_code || Number.isNaN(qty) || qty < 0) return wx.showToast({ title: "请完整填写盘点信息", icon: "none" });
    if (!this.data.goodsMatched || !this.data.locationMatched) return wx.showToast({ title: "请确认货物和库位信息", icon: "none" });

    this.setData({ loadingAddItem: true });
    try {
      const result = await request({
        url: "/check-orders/items/",
        method: "POST",
        data: { header_id: headerId, goods_barcode: this.data.itemForm.goods_barcode, location_code: this.data.itemForm.location_code, check_quantity: qty }
      });
      const diff = Number(result.diff_quantity || 0);
      const msg = Math.abs(diff) < 0.01 ? "盘点一致" : `盘点差异: ${fmtNum(diff)}`;
      this.resetCheckForm();
      await this.openDetailById(headerId);
      await this.loadOrders();

      wx.showModal({
        title: msg,
        content: "是否继续扫码下一个货物？",
        confirmText: "继续扫码",
        cancelText: "完成盘点",
        success: async (res) => { if (res.cancel) await this.completeOrder(); }
      });
    } catch (err) {
      wx.showToast({ title: err.message || "保存失败", icon: "none" });
    } finally {
      this.setData({ loadingAddItem: false });
    }
  },

  async completeOrder() {
    const headerId = this.data.selectedHeader.id;
    if (!headerId) return;
    this.setData({ loadingComplete: true });
    try {
      const res = await request({ url: `/check-orders/${headerId}/complete`, method: "POST" });
      wx.showToast({ title: res.message || "盘点完成", icon: "success" });
      await this.openDetailById(headerId);
      await this.loadOrders();
    } catch (err) {
      wx.showToast({ title: err.message || "完成失败", icon: "none" });
    } finally {
      this.setData({ loadingComplete: false });
    }
  }
});

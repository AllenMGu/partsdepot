const { request } = require("../../utils/api");
const { getUser } = require("../../utils/auth");
const { requireLogin } = require("../../utils/guard");
const { fmtNum } = require("../../utils/format");

function normalizeCheckItems(items) {
  return (items || []).map((it) => ({
    ...it,
    check_quantity: fmtNum(it.check_quantity),
    actual_quantity: fmtNum(it.actual_quantity),
    diff_quantity: fmtNum(it.diff_quantity)
  }));
}

Page({
  data: {
    labels: {
      currentWarehouse: "\u5f53\u524d\u4ed3\u5e93\uff1a",
      inventory: "\u51fa\u5165\u5e93",
      check: "\u76d8\u70b9",
      type: "\u7c7b\u578b\uff1a",
      goodsBarcode: "\u8d27\u7269\u6761\u7801",
      scanGoods: "\u626b\u7801\u8d27\u7269",
      locationCode: "\u5e93\u4f4d\u7f16\u7801\uff08\u652f\u6301\u6a21\u7cca\u641c\u7d22\uff09",
      scanLocation: "\u626b\u7801\u5e93\u4f4d",
      moreLocation: "\u67e5\u770b\u66f4\u591a\u5e93\u4f4d",
      lessLocation: "\u6536\u8d77\u5e93\u4f4d",
      currentStock: "\u5f53\u524d\u5e93\u5b58\uff1a",
      quantity: "\u6570\u91cf",
      remark: "\u5907\u6ce8(\u9009\u586b)",
      submitInventory: "\u63d0\u4ea4\u51fa\u5165\u5e93",
      checkGuide: "\u5148\u521b\u5efa\u76d8\u70b9\u5355\uff0c\u518d\u626b\u7801\u786e\u8ba4\u6570\u91cf",
      checkRemark: "\u76d8\u70b9\u5907\u6ce8(\u9009\u586b)",
      createCheckOrder: "\u521b\u5efa\u76d8\u70b9\u5355",
      checkOrderList: "\u76d8\u70b9\u5355\u5217\u8868\uff08\u5f53\u524d\u4ed3\u5e93\uff09",
      detailCount: "\u660e\u7ec6",
      scanCheck: "\u626b\u7801\u76d8\u70b9",
      currentCheckOrder: "\u5f53\u524d\u76d8\u70b9\u5355\uff1a",
      checkLocationCode: "\u5e93\u4f4d\u7f16\u7801",
      checkQty: "\u76d8\u70b9\u6570\u91cf",
      confirmQty: "\u786e\u8ba4\u6570\u91cf",
      completeCheck: "\u5b8c\u6210\u76d8\u70b9",
      saveCollapse: "\u4fdd\u5b58\u6536\u8d77",
      checkItems: "\u76d8\u70b9\u660e\u7ec6",
      noCheckItems: "\u6682\u65e0\u76d8\u70b9\u660e\u7ec6",
      systemStock: "\u5f53\u524d\u7cfb\u7edf\u5e93\u5b58\uff1a"
    },
    mode: "inventory",
    invTypes: [
      { label: "\u5165\u5e93", value: "\u5165\u5e93" },
      { label: "\u51fa\u5e93", value: "\u51fa\u5e93" }
    ],
    selectedType: { label: "\u5165\u5e93", value: "\u5165\u5e93" },
    currentWarehouseName: "",
    currentWarehouseId: null,
    warehouseLocations: [],
    locationOptions: [],
    visibleLocationOptions: [],
    hasMoreLocationOptions: false,
    locationExpanded: false,
    showLocationDropdown: false,
    currentStock: "0.00",
    inventory: { goods_barcode: "", location_code: "", quantity: "", remark: "" },

    checkOrders: [],
    checkOrder: null,
    checkOrderItems: [],
    checkExpanded: false,
    checkRemark: "",
    checkGoodsList: [],
    checkLocationList: [],
    checkGoodsMatched: false,
    checkLocationMatched: false,
    checkGoodsInfo: "",
    checkLocationInfo: "",
    checkCurrentStock: "-",
    checkForm: { goods_barcode: "", location_code: "", check_quantity: "" },

    loading: false,
    result: ""
  },

  async onShow() {
    if (!requireLogin()) return;
    const user = getUser() || {};
    this.setData({
      currentWarehouseName: user.current_warehouse_name || "",
      currentWarehouseId: user.current_warehouse_id || null
    });
    await Promise.all([this.loadCurrentWarehouseLocations(), this.loadCheckGoods(), this.loadCheckOrders()]);
  },

  setInventoryMode() { this.setData({ mode: "inventory", result: "" }); },
  setCheckMode() { this.setData({ mode: "check", result: "" }); },

  async loadCurrentWarehouseLocations() {
    if (!this.data.currentWarehouseId) return;
    try {
      const list = await request({ url: "/locations/", data: { warehouse_id: this.data.currentWarehouseId } });
      this.setData({ warehouseLocations: list || [], locationOptions: list || [] });
      this.updateVisibleLocationOptions();
    } catch (err) {
      wx.showToast({ title: err.message || "\u5e93\u4f4d\u52a0\u8f7d\u5931\u8d25", icon: "none" });
    }
  },

  onTypeChange(e) {
    const index = Number(e.detail.value);
    this.setData({ selectedType: this.data.invTypes[index] }, () => this.refreshCurrentStock());
  },
  onGoodsInput(e) { this.setData({ "inventory.goods_barcode": e.detail.value.trim() }, () => this.refreshCurrentStock()); },
  onLocationInput(e) {
    const keyword = e.detail.value.trim();
    this.setData({ "inventory.location_code": keyword }, () => {
      this.filterLocationOptions(keyword);
      this.refreshCurrentStock();
    });
  },
  onLocationFocus() { this.filterLocationOptions(this.data.inventory.location_code || ""); },
  toggleLocationExpand() { this.setData({ locationExpanded: !this.data.locationExpanded }, () => this.updateVisibleLocationOptions()); },
  chooseLocation(e) {
    const code = e.currentTarget.dataset.code;
    this.setData({ "inventory.location_code": code, showLocationDropdown: false, locationExpanded: false }, () => this.refreshCurrentStock());
  },
  updateVisibleLocationOptions() {
    const all = this.data.locationOptions || [];
    const visible = this.data.locationExpanded ? all : all.slice(0, 3);
    this.setData({ visibleLocationOptions: visible, hasMoreLocationOptions: all.length > 3 });
  },
  filterLocationOptions(keyword) {
    const kw = (keyword || "").toLowerCase();
    const options = (this.data.warehouseLocations || []).filter((item) => !kw ? true : `${item.location_code}|${item.name}`.toLowerCase().includes(kw));
    this.setData({ locationOptions: options, showLocationDropdown: options.length > 0, locationExpanded: false });
    this.updateVisibleLocationOptions();
  },
  onQuantityInput(e) { this.setData({ "inventory.quantity": e.detail.value.trim() }); },
  onRemarkInput(e) { this.setData({ "inventory.remark": e.detail.value }); },

  scanGoodsCode() { this.scanCode((code) => this.setData({ "inventory.goods_barcode": code }, () => this.refreshCurrentStock())); },
  scanLocationCode() { this.scanCode((code) => this.setData({ "inventory.location_code": code }, () => { this.filterLocationOptions(code); this.refreshCurrentStock(); })); },

  async refreshCurrentStock() {
    if (this.data.selectedType.value !== "\u51fa\u5e93") return this.setData({ currentStock: "0.00" });
    const barcode = this.data.inventory.goods_barcode;
    if (!barcode) return this.setData({ currentStock: "0.00" });
    try {
      const stockList = await request({ url: "/stock/" });
      const locationCode = this.data.inventory.location_code;
      const rows = (stockList || []).filter((row) => {
        const sameWarehouse = this.data.currentWarehouseName ? row.warehouse_name === this.data.currentWarehouseName : true;
        const sameGoods = row.goods_barcode === barcode;
        const sameLocation = locationCode ? row.location_code === locationCode : true;
        return sameWarehouse && sameGoods && sameLocation;
      });
      this.setData({ currentStock: fmtNum(rows.reduce((sum, row) => sum + Number(row.quantity || 0), 0)) });
    } catch (_) { this.setData({ currentStock: "0.00" }); }
  },

  async submitInventory() {
    const payload = {
      goods_barcode: this.data.inventory.goods_barcode,
      location_code: this.data.inventory.location_code,
      type: this.data.selectedType.value,
      quantity: Number(this.data.inventory.quantity || 0),
      remark: this.data.inventory.remark
    };
    if (!payload.goods_barcode || !payload.location_code || payload.quantity <= 0) return wx.showToast({ title: "\u8bf7\u5b8c\u6574\u586b\u5199\u4fe1\u606f", icon: "none" });
    if (payload.type === "\u51fa\u5e93" && payload.quantity > Number(this.data.currentStock || 0)) return wx.showToast({ title: "\u51fa\u5e93\u6570\u91cf\u4e0d\u80fd\u5927\u4e8e\u5f53\u524d\u5e93\u5b58", icon: "none" });

    this.setData({ loading: true, result: "" });
    try {
      const res = await request({ url: "/inventory/scan", method: "POST", data: payload });
      this.setData({ result: `${res.message}\uff0c\u8d27\u7269:${res.goods_name}\uff0c\u5e93\u4f4d:${res.location_name}\uff0c\u5f53\u524d\u5e93\u5b58:${res.current_stock}` });
      wx.showToast({ title: "\u63d0\u4ea4\u6210\u529f", icon: "success" });
      this.refreshCurrentStock();
    } catch (err) {
      wx.showToast({ title: err.message || "\u63d0\u4ea4\u5931\u8d25", icon: "none" });
    } finally { this.setData({ loading: false }); }
  },

  async loadCheckGoods() {
    try {
      const list = await request({ url: "/goods/" });
      this.setData({ checkGoodsList: list || [] });
    } catch (err) {
      wx.showToast({ title: err.message || "\u8d27\u7269\u52a0\u8f7d\u5931\u8d25", icon: "none" });
    }
  },

  async loadCheckOrders() {
    try {
      const res = await request({ url: "/check-orders/", data: { warehouse_id: this.data.currentWarehouseId || undefined } });
      this.setData({ checkOrders: (res && res.data) || [] });
    } catch (err) {
      wx.showToast({ title: err.message || "\u76d8\u70b9\u5355\u52a0\u8f7d\u5931\u8d25", icon: "none" });
    }
  },

  onCheckRemarkInput(e) { this.setData({ checkRemark: e.detail.value }); },

  async createCheckOrder() {
    this.setData({ loading: true });
    try {
      const created = await request({
        url: "/check-orders/",
        method: "POST",
        data: { warehouse_id: this.data.currentWarehouseId || null, remark: this.data.checkRemark || null }
      });
      await this.openCheckOrder(created.id);
      await this.loadCheckOrders();
      this.setData({ checkRemark: "" });
      wx.showToast({ title: "\u76d8\u70b9\u5355\u5df2\u521b\u5efa", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "\u521b\u5efa\u5931\u8d25", icon: "none" });
    } finally { this.setData({ loading: false }); }
  },

  selectCheckOrder(e) { this.openCheckOrder(Number(e.currentTarget.dataset.id)); },

  async openCheckOrder(id) {
    if (!id) return;
    try {
      const detail = await request({ url: `/check-orders/${id}` });
      this.setData({ checkOrder: detail.header || null, checkOrderItems: normalizeCheckItems(detail.items), checkExpanded: true });
      const warehouseId = detail.header ? detail.header.warehouse_id : null;
      if (warehouseId) {
        const list = await request({ url: "/locations/", data: { warehouse_id: warehouseId } });
        this.setData({ checkLocationList: list || [] });
      }
      this.resetCheckForm();
    } catch (err) {
      wx.showToast({ title: err.message || "\u6253\u5f00\u76d8\u70b9\u5355\u5931\u8d25", icon: "none" });
    }
  },

  async saveAndCollapseCheck() {
    if (!this.data.checkOrder || !this.data.checkOrder.id) return this.setData({ checkExpanded: false });
    try {
      const detail = await request({ url: `/check-orders/${this.data.checkOrder.id}` });
      this.setData({ checkOrderItems: normalizeCheckItems(detail.items) });
      wx.showToast({ title: "\u5df2\u4fdd\u5b58\u5e76\u6536\u8d77", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "\u4fdd\u5b58\u5931\u8d25", icon: "none" });
    } finally { this.setData({ checkExpanded: false }); }
  },

  onCheckGoodsInput(e) { this.setData({ "checkForm.goods_barcode": e.detail.value.trim() }, () => this.validateCheckGoodsAndStock()); },
  onCheckLocationInput(e) { this.setData({ "checkForm.location_code": e.detail.value.trim() }, () => this.validateCheckLocationAndStock()); },
  onCheckQtyInput(e) { this.setData({ "checkForm.check_quantity": e.detail.value.trim() }); },
  scanCheckGoodsCode() { this.scanCode((code) => this.setData({ "checkForm.goods_barcode": code }, () => this.validateCheckGoodsAndStock())); },
  scanCheckLocationCode() { this.scanCode((code) => this.setData({ "checkForm.location_code": code }, () => this.validateCheckLocationAndStock())); },

  scanCode(onSuccess) {
    wx.scanCode({ onlyFromCamera: false, success: (res) => onSuccess(res.result || ""), fail: () => wx.showToast({ title: "\u626b\u7801\u53d6\u6d88/\u5931\u8d25", icon: "none" }) });
  },

  validateCheckGoodsAndStock() {
    const code = this.data.checkForm.goods_barcode;
    const goods = (this.data.checkGoodsList || []).find((g) => g.barcode === code);
    if (!goods) return this.setData({ checkGoodsMatched: false, checkGoodsInfo: code ? "\u8d27\u7269\u4e0d\u5b58\u5728" : "", checkCurrentStock: "-" });
    this.setData({ checkGoodsMatched: true, checkGoodsInfo: `${goods.name} | ${goods.spec || "\u65e0\u89c4\u683c"} | ${goods.unit}` });
    this.refreshCheckCurrentStock();
  },

  validateCheckLocationAndStock() {
    const code = this.data.checkForm.location_code;
    const loc = (this.data.checkLocationList || []).find((l) => l.location_code === code);
    if (!loc) return this.setData({ checkLocationMatched: false, checkLocationInfo: code ? "\u5e93\u4f4d\u4e0d\u5b58\u5728\u6216\u4e0d\u5c5e\u4e8e\u8be5\u76d8\u70b9\u5355\u4ed3\u5e93" : "", checkCurrentStock: "-" });
    this.setData({ checkLocationMatched: true, checkLocationInfo: `${loc.location_code} | ${loc.name}` });
    this.refreshCheckCurrentStock();
  },

  async refreshCheckCurrentStock() {
    if (!this.data.checkGoodsMatched || !this.data.checkLocationMatched || !this.data.checkOrder) return this.setData({ checkCurrentStock: "-" });
    try {
      const stockList = await request({ url: "/stock/" });
      const rows = (stockList || []).filter((row) => row.goods_barcode === this.data.checkForm.goods_barcode && row.location_code === this.data.checkForm.location_code && row.warehouse_name === this.data.checkOrder.warehouse_name);
      this.setData({ checkCurrentStock: fmtNum(rows.reduce((sum, row) => sum + Number(row.quantity || 0), 0)) });
    } catch (_) { this.setData({ checkCurrentStock: "-" }); }
  },

  resetCheckForm() {
    this.setData({
      checkForm: { goods_barcode: "", location_code: "", check_quantity: "" },
      checkGoodsMatched: false,
      checkLocationMatched: false,
      checkGoodsInfo: "",
      checkLocationInfo: "",
      checkCurrentStock: "-"
    });
  },

  async confirmCheckItem() {
    if (!this.data.checkOrder || !this.data.checkOrder.id) return wx.showToast({ title: "\u8bf7\u5148\u521b\u5efa\u76d8\u70b9\u5355", icon: "none" });
    const payload = {
      header_id: this.data.checkOrder.id,
      goods_barcode: this.data.checkForm.goods_barcode,
      location_code: this.data.checkForm.location_code,
      check_quantity: Number(this.data.checkForm.check_quantity || 0)
    };
    if (!payload.goods_barcode || !payload.location_code || Number.isNaN(payload.check_quantity) || payload.check_quantity < 0) return wx.showToast({ title: "\u8bf7\u5b8c\u6574\u586b\u5199\u76d8\u70b9\u4fe1\u606f", icon: "none" });
    if (!this.data.checkGoodsMatched || !this.data.checkLocationMatched) return wx.showToast({ title: "\u8bf7\u786e\u8ba4\u8d27\u7269\u548c\u5e93\u4f4d", icon: "none" });

    this.setData({ loading: true });
    try {
      const res = await request({ url: "/check-orders/items/", method: "POST", data: payload });
      const diff = Number(res.diff_quantity || 0);
      const title = Math.abs(diff) < 0.01 ? "\u76d8\u70b9\u4e00\u81f4" : `\u76d8\u70b9\u5dee\u5f02: ${fmtNum(diff)}`;
      this.resetCheckForm();
      await this.openCheckOrder(this.data.checkOrder.id);
      await this.loadCheckOrders();
      wx.showModal({
        title,
        content: "\u786e\u8ba4\u6570\u91cf\u6210\u529f\uff0c\u662f\u5426\u7ee7\u7eed\u626b\u7801\u4e0b\u4e00\u4e2a\u8d27\u7269\uff1f",
        confirmText: "\u7ee7\u7eed\u626b\u7801",
        cancelText: "\u5b8c\u6210\u76d8\u70b9",
        success: async (mRes) => { if (mRes.cancel) await this.completeCheckOrder(); }
      });
    } catch (err) {
      wx.showToast({ title: err.message || "\u76d8\u70b9\u5931\u8d25", icon: "none" });
    } finally { this.setData({ loading: false }); }
  },

  async completeCheckOrder() {
    if (!this.data.checkOrder || !this.data.checkOrder.id) return wx.showToast({ title: "\u8bf7\u5148\u521b\u5efa\u76d8\u70b9\u5355", icon: "none" });
    this.setData({ loading: true });
    try {
      const res = await request({ url: `/check-orders/${this.data.checkOrder.id}/complete`, method: "POST" });
      wx.showToast({ title: res.message || "\u76d8\u70b9\u5b8c\u6210", icon: "success" });
      await this.openCheckOrder(this.data.checkOrder.id);
      await this.loadCheckOrders();
      this.setData({ checkExpanded: false });
    } catch (err) {
      wx.showToast({ title: err.message || "\u5b8c\u6210\u5931\u8d25", icon: "none" });
    } finally { this.setData({ loading: false }); }
  }
});

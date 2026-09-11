const { request } = require("../../utils/api");
const { requireLogin } = require("../../utils/guard");
const { fmtNum, fmtDateTime } = require("../../utils/format");

function normalizeOrder(order) {
  return Object.assign({}, order, {
    total_amount: fmtNum(order.total_amount || 0),
    create_time_fmt: fmtDateTime(order.create_time)
  });
}

Page({
  data: {
    orders: [],
    selected: {},
    createForm: { customer: "", remark: "" },
    itemForm: { goods_barcode: "", location_code: "", quantity: "", unit_price: "", remark: "" },
    loadingCreate: false,
    loadingAddItem: false,
    loadingSubmit: false,
    loadingDelete: false
  },
  async onShow() {
    if (!requireLogin()) return;
    await this.loadOrders();
  },
  onCustomerInput(e) {
    this.setData({ "createForm.customer": e.detail.value });
  },
  onCreateRemarkInput(e) {
    this.setData({ "createForm.remark": e.detail.value });
  },
  onItemBarcodeInput(e) {
    this.setData({ "itemForm.goods_barcode": e.detail.value.trim() });
  },
  onItemLocationInput(e) {
    this.setData({ "itemForm.location_code": e.detail.value.trim() });
  },
  onItemQtyInput(e) {
    this.setData({ "itemForm.quantity": e.detail.value.trim() });
  },
  onItemPriceInput(e) {
    this.setData({ "itemForm.unit_price": e.detail.value.trim() });
  },
  onItemRemarkInput(e) {
    this.setData({ "itemForm.remark": e.detail.value });
  },
  async loadOrders() {
    wx.showLoading({ title: "加载中" });
    try {
      const res = await request({ url: "/outbound-orders/" });
      this.setData({ orders: (res || []).map(normalizeOrder) });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
  async createOrder() {
    this.setData({ loadingCreate: true });
    try {
      const body = {
        customer: this.data.createForm.customer,
        remark: this.data.createForm.remark
      };
      await request({ url: "/outbound-orders/", method: "POST", data: body });
      wx.showToast({ title: "创建成功", icon: "success" });
      this.setData({ createForm: { customer: "", remark: "" } });
      await this.loadOrders();
    } catch (err) {
      wx.showToast({ title: err.message || "创建失败", icon: "none" });
    } finally {
      this.setData({ loadingCreate: false });
    }
  },
  async openDetail(e) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    wx.showLoading({ title: "加载详情" });
    try {
      const detail = await request({ url: `/outbound-orders/${id}` });
      detail.total_amount = fmtNum(detail.total_amount || 0);
      detail.items = (detail.items || []).map((it) =>
        Object.assign({}, it, {
          quantity: fmtNum(it.quantity),
          unit_price: fmtNum(it.unit_price),
          total_price: fmtNum(it.total_price)
        })
      );
      this.setData({ selected: detail });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
  closeDetail() {
    this.setData({ selected: {} });
  },
  async addItem() {
    const orderId = this.data.selected.id;
    const form = this.data.itemForm;
    if (!orderId) return;
    const qty = Number(form.quantity || 0);
    if (!form.goods_barcode || !form.location_code || qty <= 0) {
      wx.showToast({ title: "请完整填写明细", icon: "none" });
      return;
    }
    const unitPrice = form.unit_price === "" ? null : Number(form.unit_price);
    this.setData({ loadingAddItem: true });
    try {
      await request({
        url: `/outbound-orders/${orderId}/items`,
        method: "POST",
        data: {
          goods_barcode: form.goods_barcode,
          location_code: form.location_code,
          quantity: qty,
          unit_price: unitPrice,
          remark: form.remark || ""
        }
      });
      wx.showToast({ title: "已添加", icon: "success" });
      this.setData({
        itemForm: { goods_barcode: "", location_code: "", quantity: "", unit_price: "", remark: "" }
      });
      await this.openDetail({ currentTarget: { dataset: { id: orderId } } });
      await this.loadOrders();
    } catch (err) {
      wx.showToast({ title: err.message || "添加失败", icon: "none" });
    } finally {
      this.setData({ loadingAddItem: false });
    }
  },
  async deleteItem(e) {
    const orderId = this.data.selected.id;
    const itemId = Number(e.currentTarget.dataset.itemid);
    if (!orderId || !itemId) return;
    this.setData({ loadingAddItem: true });
    try {
      await request({ url: `/outbound-orders/${orderId}/items/${itemId}`, method: "DELETE" });
      wx.showToast({ title: "已删除", icon: "success" });
      await this.openDetail({ currentTarget: { dataset: { id: orderId } } });
      await this.loadOrders();
    } catch (err) {
      wx.showToast({ title: err.message || "删除失败", icon: "none" });
    } finally {
      this.setData({ loadingAddItem: false });
    }
  },
  async submitOrder() {
    const orderId = this.data.selected.id;
    if (!orderId) return;
    this.setData({ loadingSubmit: true });
    try {
      await request({ url: `/outbound-orders/${orderId}/submit`, method: "POST" });
      wx.showToast({ title: "提交成功", icon: "success" });
      await this.openDetail({ currentTarget: { dataset: { id: orderId } } });
      await this.loadOrders();
    } catch (err) {
      wx.showToast({ title: err.message || "提交失败", icon: "none" });
    } finally {
      this.setData({ loadingSubmit: false });
    }
  },
  async deleteOrder() {
    const orderId = this.data.selected.id;
    if (!orderId) return;
    this.setData({ loadingDelete: true });
    try {
      await request({ url: `/outbound-orders/${orderId}`, method: "DELETE" });
      wx.showToast({ title: "已删除", icon: "success" });
      this.setData({ selected: {} });
      await this.loadOrders();
    } catch (err) {
      wx.showToast({ title: err.message || "删除失败", icon: "none" });
    } finally {
      this.setData({ loadingDelete: false });
    }
  }
});

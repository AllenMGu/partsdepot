const { request } = require("../../utils/api");

Page({
  data: {
    applicantName: "",
    department: "",
    departments: ["行政事务部", "工程管理部", "安全管理部", "生产技术部", "质量管理", "综合管理部"],
    departmentIndex: -1,
    description: "",
    attachmentNote: "",
    warehouses: [],
    warehouseIndex: -1,
    warehouseId: null,
    warehouseLoading: false,
    warehouseError: "",
    goodsItems: [],
    goodsKeyword: "",
    goodsResults: [],
    searching: false,
    searchError: "",
    submitting: false
  },

  onLoad() {
    this.loadWarehouses();
  },

  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this._searchSeq = (this._searchSeq || 0) + 1;
  },

  // ---------- 表单输入 ----------
  onApplicantInput(e) { this.setData({ applicantName: e.detail.value }); },
  onDepartmentChange(e) {
    const index = Number(e.detail.value);
    const department = this.data.departments[index];
    if (!department) return;
    this.setData({ departmentIndex: index, department });
  },
  onDescriptionInput(e) { this.setData({ description: e.detail.value }); },
  onAttachmentNoteInput(e) { this.setData({ attachmentNote: e.detail.value }); },

  async loadWarehouses() {
    this.setData({ warehouseLoading: true, warehouseError: "" });
    try {
      const warehouses = await request({ url: "/public/warehouses", withToken: false });
      const rows = Array.isArray(warehouses) ? warehouses : [];
      this.setData({
        warehouses: rows,
        warehouseIndex: rows.length ? 0 : -1,
        warehouseId: rows.length ? rows[0].id : null,
        warehouseError: rows.length ? "" : "当前没有可用仓库，请联系管理员"
      });
    } catch (e) {
      this.setData({ warehouseError: e.message || "仓库列表加载失败" });
    } finally {
      this.setData({ warehouseLoading: false });
    }
  },

  onWarehouseChange(e) {
    const index = Number(e.detail.value);
    const warehouse = this.data.warehouses[index];
    if (!warehouse) return;
    this._searchSeq = (this._searchSeq || 0) + 1;
    this._warehouseSeq = (this._warehouseSeq || 0) + 1;
    this.setData({
      warehouseIndex: index,
      warehouseId: warehouse.id,
      goodsKeyword: "",
      goodsResults: [],
      searching: false,
      searchError: ""
    }, () => this.refreshPickedStock(this._warehouseSeq));
  },

  async refreshPickedStock(seq) {
    const items = this.data.goodsItems;
    if (!items.length || !this.data.warehouseId) return;
    // 给本次请求覆盖的行打标记。请求期间新增、删除后重加的行没有该标记，旧响应不得覆盖它们。
    const markedItems = items.map((g) => Object.assign({}, g, {
      stockUnknown: true,
      stockRefreshSeq: seq
    }));
    this.setData({ goodsItems: markedItems });
    try {
      const rows = await request({
        url: "/public/stock-lookup",
        method: "POST",
        data: { warehouse_id: this.data.warehouseId, barcodes: markedItems.map((g) => g.barcode) },
        withToken: false
      });
      if (seq !== this._warehouseSeq) return;
      const byCode = {};
      (Array.isArray(rows) ? rows : []).forEach((row) => { byCode[row.barcode] = Number(row.available_stock) || 0; });
      this.setData({
        goodsItems: this.data.goodsItems.map((g) => {
          if (g.stockRefreshSeq !== seq) return g;
          return Object.assign({}, g, {
            availableStock: byCode[g.barcode] || 0,
            stockUnknown: !Object.prototype.hasOwnProperty.call(byCode, g.barcode),
            stockRefreshSeq: null
          });
        })
      });
    } catch (e) {
      if (seq !== this._warehouseSeq) return;
      wx.showToast({ title: "库存刷新失败，已标记未知", icon: "none" });
    }
  },

  // ---------- 货物搜索（防抖 350ms + 仅接受最新请求） ----------
  onGoodsKeyword(e) {
    const kw = e.detail.value.trim();
    this._searchSeq = (this._searchSeq || 0) + 1;
    const seq = this._searchSeq;

    this.setData({ goodsKeyword: kw, goodsResults: [], searchError: "" });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    if (!kw) {
      this.setData({ searching: false });
      return;
    }
    this._searchTimer = setTimeout(() => this.searchGoods(kw, seq), 350);
  },

  async searchGoods(kw, seq) {
    if (seq !== this._searchSeq) return;
    this.setData({ searching: true, searchError: "" });
    try {
      const data = await request({
        url: "/public/goods-search",
        data: { q: kw, warehouse_id: this.data.warehouseId },
        withToken: false
      });
      if (seq !== this._searchSeq) return;
      this.setData({ goodsResults: data || [] });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this.setData({ goodsResults: [], searchError: err.message || "搜索失败" });
    } finally {
      if (seq === this._searchSeq) {
        this.setData({ searching: false });
      }
    }
  },

  // ---------- 货物行管理 ----------
  onPickGoods(e) {
    const g = e.currentTarget.dataset.goods;
    const exists = this.data.goodsItems.some((x) => x.barcode === g.barcode);
    if (exists) {
      wx.showToast({ title: "该货物已在清单中", icon: "none" });
      return;
    }
    const goodsItems = this.data.goodsItems.concat([
      {
        barcode: g.barcode,
        name: g.name,
        spec: g.spec || "",
        unit: g.unit || "",
        qty: "1",
        availableStock: Number(g.available_stock) || 0,
        stockUnknown: false
      }
    ]);
    this._searchSeq = (this._searchSeq || 0) + 1;
    this.setData({
      goodsItems,
      goodsResults: [],
      goodsKeyword: "",
      searching: false,
      searchError: ""
    });
  },

  onQtyInput(e) {
    const idx = e.currentTarget.dataset.idx;
    const goodsItems = this.data.goodsItems.slice();
    goodsItems[idx].qty = e.detail.value;
    this.setData({ goodsItems });
  },

  onRemoveGoods(e) {
    const idx = e.currentTarget.dataset.idx;
    const goodsItems = this.data.goodsItems.slice();
    goodsItems.splice(idx, 1);
    this.setData({ goodsItems });
  },

  // ---------- 校验与提交 ----------
  validate() {
    const d = this.data;
    const applicantName = d.applicantName.trim();
    const description = d.description.trim();

    if (!d.warehouseId) return "请选择申请仓库";
    if (!applicantName) return "请填写申请人";
    if (!description) return "请填写事由描述";
    for (let i = 0; i < d.goodsItems.length; i++) {
      const q = Number(d.goodsItems[i].qty);
      if (d.goodsItems[i].qty === "" || isNaN(q) || q <= 0) {
        return `请填写第 ${i + 1} 行货物（${d.goodsItems[i].name}）的数量（大于 0）`;
      }
    }
    return "";
  },

  async submit() {
    if (this.data.submitting) return;

    const err = this.validate();
    if (err) {
      wx.showToast({ title: err, icon: "none" });
      return;
    }
    this.setData({ submitting: true });
    try {
      const applicantName = this.data.applicantName.trim();
      const department = this.data.department.trim();
      const description = this.data.description.trim();
      const attachmentNote = this.data.attachmentNote.trim();

      const payload = {
        applicant_name: applicantName,
        description,
        warehouse_id: this.data.warehouseId,
        items: this.data.goodsItems.map((g) => ({ barcode: g.barcode, quantity: Number(g.qty) }))
      };
      if (department) payload.department = department;
      if (attachmentNote) payload.attachment_note = attachmentNote;

      const res = await request({ url: "/requests/", method: "POST", data: payload, withToken: false });
      wx.showModal({
        title: "提交成功",
        content: `申请编号：${res.reference || ""}\n请保存申请编号，处理进展请联系受理管理员跟进。`,
        showCancel: false,
        success: () => this.resetForm()
      });
    } catch (e) {
      wx.showToast({ title: e.message || "提交失败", icon: "none" });
    } finally {
      this.setData({ submitting: false });
    }
  },

  resetForm() {
    this.setData({
      applicantName: "",
      department: "",
      departmentIndex: -1,
      description: "",
      attachmentNote: "",
      goodsItems: [],
      goodsResults: [],
      goodsKeyword: "",
      searchError: ""
    });
  }
});

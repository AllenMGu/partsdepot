const { request } = require("../../utils/api");

// 与后端 core/schemas.py 的 EMAIL_PATTERN 保持一致（ASCII 邮箱）
const EMAIL_RE = /^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$/;

Page({
  data: {
    applicantName: "",
    department: "",
    contact: "",
    description: "",
    attachmentNote: "",
    goodsItems: [],
    goodsKeyword: "",
    goodsResults: [],
    searching: false,
    searchError: "",
    submitting: false
  },

  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
  },

  // ---------- 表单输入 ----------
  onApplicantInput(e) { this.setData({ applicantName: e.detail.value.trim() }); },
  onDepartmentInput(e) { this.setData({ department: e.detail.value.trim() }); },
  onContactInput(e) { this.setData({ contact: e.detail.value.trim() }); },
  onDescriptionInput(e) { this.setData({ description: e.detail.value.trim() }); },
  onAttachmentNoteInput(e) { this.setData({ attachmentNote: e.detail.value.trim() }); },

  // ---------- 货物搜索（防抖 350ms） ----------
  onGoodsKeyword(e) {
    const kw = e.detail.value.trim();
    this.setData({ goodsKeyword: kw, goodsResults: [], searchError: "" });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    if (!kw) return;
    this._searchTimer = setTimeout(() => this.searchGoods(kw), 350);
  },

  async searchGoods(kw) {
    this.setData({ searching: true, searchError: "" });
    try {
      const data = await request({
        url: "/public/goods-search",
        data: { q: kw },
        withToken: false
      });
      this.setData({ goodsResults: data || [] });
    } catch (err) {
      this.setData({ goodsResults: [], searchError: err.message || "搜索失败" });
    } finally {
      this.setData({ searching: false });
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
      { barcode: g.barcode, name: g.name, spec: g.spec || "", unit: g.unit || "", qty: "1" }
    ]);
    this.setData({ goodsItems, goodsResults: [], goodsKeyword: "" });
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
    if (!d.applicantName) return "请填写申请人";
    if (!d.contact) return "请填写联系邮箱";
    if (!EMAIL_RE.test(d.contact)) return "邮箱格式不正确（示例：zhangsan@example.com）";
    if (!d.description) return "请填写事由描述";
    for (let i = 0; i < d.goodsItems.length; i++) {
      const q = Number(d.goodsItems[i].qty);
      if (d.goodsItems[i].qty === "" || isNaN(q) || q <= 0) {
        return `请填写第 ${i + 1} 行货物（${d.goodsItems[i].name}）的数量（大于 0）`;
      }
    }
    return "";
  },

  async submit() {
    const err = this.validate();
    if (err) {
      wx.showToast({ title: err, icon: "none" });
      return;
    }
    this.setData({ submitting: true });
    try {
      const payload = {
        applicant_name: this.data.applicantName,
        contact: this.data.contact,
        description: this.data.description,
        items: this.data.goodsItems.map((g) => ({ barcode: g.barcode, quantity: Number(g.qty) }))
      };
      if (this.data.department) payload.department = this.data.department;
      if (this.data.attachmentNote) payload.attachment_note = this.data.attachmentNote;

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
      contact: "",
      description: "",
      attachmentNote: "",
      goodsItems: [],
      goodsResults: [],
      goodsKeyword: ""
    });
  }
});

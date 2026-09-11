const { request } = require("../../utils/api");
const { getUser, saveAuth, getToken, getExpiry, clearAuth } = require("../../utils/auth");
const { requireLogin } = require("../../utils/guard");

Page({
  data: {
    user: {},
    warehouses: []
  },
  onShow() {
    if (!requireLogin()) return;
    this.reloadUser();
  },
  reloadUser() {
    const user = getUser() || {};
    this.setData({
      user,
      warehouses: user.warehouses || []
    });
  },
  async switchWarehouse(e) {
    const warehouseId = Number(e.currentTarget.dataset.id);
    const warehouseName = e.currentTarget.dataset.name;
    const user = this.data.user;
    if (!warehouseId || !user.id) return;

    wx.showLoading({ title: "切换中" });
    try {
      await request({
        url: `/users/${user.id}/switch-warehouse?warehouse_id=${warehouseId}`,
        method: "POST"
      });

      const newUser = Object.assign({}, user, {
        current_warehouse_id: warehouseId,
        current_warehouse_name: warehouseName
      });
      saveAuth({
        access_token: getToken(),
        expiry: getExpiry(),
        user: newUser
      });
      getApp().globalData.user = newUser;
      this.reloadUser();
      wx.showToast({ title: "切换成功", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "切换失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
  logout() {
    clearAuth();
    getApp().globalData.user = null;
    getApp().globalData.token = "";
    wx.reLaunch({ url: "/pages/login/index" });
  }
});

/* 入库单页 */
window.M_PAGES["inbound"] = window.createWmsOrderPage({
  key: "inbound",
  apiBase: "/inbound-orders",
  partnerLabel: "供应商",
  partnerField: "supplier",
  hasReturn: true
});

/* 出库单页 */
window.M_PAGES["outbound"] = window.createWmsOrderPage({
  key: "outbound",
  apiBase: "/outbound-orders",
  partnerLabel: "客户",
  partnerField: "customer",
  hasReturn: false
});

/* 单据中心（对标小程序 pages/orders 入口页） */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["orders"] = function () {
  var M = window.M;
  if (!M.AUTH.require()) return;

  window.M_ACTIONS["goInbound"] = function () { window.location.href = "inbound.html"; };
  window.M_ACTIONS["goOutbound"] = function () { window.location.href = "outbound.html"; };
  window.M_ACTIONS["goCheck"] = function () { window.location.href = "check.html"; };
};

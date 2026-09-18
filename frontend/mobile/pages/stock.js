/* 库存查询页（对标小程序 pages/stock） */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["stock"] = function () {
  var M = window.M;
  if (!M.AUTH.require()) return;

  var rawList = [];
  var elKw = document.getElementById("mStockKw");
  var elList = document.getElementById("mStockList");
  var elCount = document.getElementById("mStockCount");

  elKw.addEventListener("input", applyFilter);

  loadStock();

  function loadStock() {
    elList.innerHTML = '<div class="m-empty">加载中…</div>';
    M.api("GET", "/stock/").then(function (rows) {
      var w = M.AUTH.currentWarehouse();
      var warehouseName = w ? w.name : "";
      rawList = (rows || []).filter(function (it) {
        return warehouseName ? (it.warehouse_name === warehouseName) : true;
      });
      elCount.textContent = "共 " + rawList.length + " 条";
      applyFilter();
    }).catch(function (err) {
      elList.innerHTML = '<div class="m-empty">' + M.esc(err.message || "加载失败") + "</div>";
    });
  }

  function applyFilter() {
    var kw = (elKw.value || "").trim().toLowerCase();
    var list = rawList;
    if (kw) {
      list = rawList.filter(function (it) {
        return [it.goods_barcode, it.goods_name, it.location_code, it.warehouse_name]
          .join("|").toLowerCase().indexOf(kw) !== -1;
      });
    }
    elCount.textContent = kw ? "匹配 " + list.length + " 条" : "共 " + rawList.length + " 条";
    if (!list.length) {
      elList.innerHTML = '<div class="m-empty">暂无库存数据</div>';
      return;
    }
    elList.innerHTML = list.map(function (it) {
      return '<div class="m-item">' +
        '<div class="m-row between">' +
        '<div class="m-grow"><div class="m-item-title">' + M.esc(it.goods_name) + "</div>" +
        '<div class="m-item-sub m-mono">' + M.esc(it.goods_barcode) +
        (it.location_code ? " · 库位 " + M.esc(it.location_code) : "") +
        (it.warehouse_name ? " · " + M.esc(it.warehouse_name) : "") + "</div></div>" +
        '<div class="m-qty-big m-mono m-mono">' + M.fmtNum(it.quantity) + "</div>" +
        "</div>" +
        '<div class="m-item-sub m-mt8">更新 ' + M.fmtDT(it.update_time) + "</div>" +
        "</div>";
    }).join("");
  }

  window.M_ACTIONS["stockRefresh"] = loadStock;
};

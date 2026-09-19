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
    // 只按当前仓库查询；关键字筛选基于完整快照，便于清空后恢复列表。
    var w = M.AUTH.currentWarehouse();
    var params = [];
    if (w && w.id) params.push("warehouse_id=" + encodeURIComponent(w.id));
    M.api("GET", "/stock/" + (params.length ? "?" + params.join("&") : "")).then(function (rows) {
      rawList = rows || [];
      elCount.textContent = "共 " + rawList.length + " 条";
      applyFilter();
    }).catch(function (err) {
      elList.innerHTML = '<div class="m-empty">' + M.esc(err.message || "加载失败") + "</div>";
    });
  }

  window.M_ACTIONS["stockScan"] = function () {
    M.scanCode(function (code) {
      elKw.value = String(code || "").trim();
      // 保留完整库存快照，搜索框清空后才能恢复全量列表。
      loadStock();
    });
  };

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

  // 全局 data-act 事件会把按钮 DOM 作为第一个参数传入，不能直接绑定 loadStock。
  window.M_ACTIONS["stockRefresh"] = function () { loadStock(); };
};

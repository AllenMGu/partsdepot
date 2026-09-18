/* 出入库日志页（对标小程序 pages/logs） */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["logs"] = function () {
  var M = window.M;
  if (!M.AUTH.require()) return;

  var elList = document.getElementById("mLogsList");
  var elCount = document.getElementById("mLogsCount");

  loadLogs();

  function loadLogs() {
    elList.innerHTML = '<div class="m-empty">加载中…</div>';
    // 按当前仓库查询（管理员默认可见全部仓库，这里统一为"当前仓库"语义）
    var w = M.AUTH.currentWarehouse();
    var wh = (w && w.id) ? "&warehouse_id=" + w.id : "";
    M.api("GET", "/inventory/logs?limit=50" + wh).then(function (rows) {
      var list = rows || [];
      elCount.textContent = "最近 " + list.length + " 条";
      if (!list.length) {
        elList.innerHTML = '<div class="m-empty">暂无出入库记录</div>';
        return;
      }
      elList.innerHTML = list.map(function (it) {
        return '<div class="m-item">' +
          '<div class="m-row between">' +
          '<div class="m-grow">' + M.typeBadge(it.type) +
          ' <span style="font-size:14px;font-weight:500;margin-left:6px;">' + M.esc(it.goods_name) + "</span></div>" +
          '<div class="m-qty-big m-mono" style="font-size:16px;">' +
          (it.type === "出库" || it.type === "OUT" ? "-" : "+") + M.fmtNum(it.quantity) + "</div>" +
          "</div>" +
          '<div class="m-item-sub m-mono m-mt8">' +
          M.esc(it.goods_barcode || "") +
          (it.location_code ? " · 库位 " + M.esc(it.location_code) : "") +
          (it.warehouse_name ? " · " + M.esc(it.warehouse_name) : "") +
          (it.operator_name ? " · " + M.esc(it.operator_name) : "") +
          "</div>" +
          '<div class="m-item-sub m-mt8">' + M.fmtDT(it.create_time) +
          (it.remark ? " · " + M.esc(it.remark) : "") + "</div>" +
          "</div>";
      }).join("");
    }).catch(function (err) {
      elList.innerHTML = '<div class="m-empty">' + M.esc(err.message || "加载失败") + "</div>";
    });
  }

  window.M_ACTIONS["logsRefresh"] = loadLogs;
};

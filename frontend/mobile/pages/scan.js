/* 扫码出入库页（对标小程序 pages/scan 的出入库模式）
 * 盘点模式入口指向 check.html（盘点单页），与小程序功能等价、避免重复实现。 */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["scan"] = function () {
  var M = window.M;
  if (!M.AUTH.require()) return;

  var state = {
    type: "入库",
    goodsBarcode: "",
    locationCode: "",
    quantity: "",
    remark: "",
    locations: [],
    locationsLoaded: false,
    currentStock: null
  };

  var elType1 = document.getElementById("mScanTypeIn");
  var elType2 = document.getElementById("mScanTypeOut");
  var elBarcode = document.getElementById("mScanBarcode");
  var elLocation = document.getElementById("mScanLocation");
  var elLocOptions = document.getElementById("mScanLocOptions");
  var elQty = document.getElementById("mScanQty");
  var elRemark = document.getElementById("mScanRemark");
  var elStock = document.getElementById("mScanStock");
  var elResult = document.getElementById("mScanResult");

  elType1.addEventListener("click", function () { setType("入库"); });
  elType2.addEventListener("click", function () { setType("出库"); });
  elBarcode.addEventListener("input", function () {
    state.goodsBarcode = elBarcode.value.trim();
    refreshStock();
  });
  elLocation.addEventListener("input", function () {
    state.locationCode = elLocation.value.trim();
    renderLocOptions();
    refreshStock();
  });
  elLocOptions.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-loc-code]") : null;
    if (!t) return;
    elLocation.value = t.getAttribute("data-loc-code");
    state.locationCode = elLocation.value;
    elLocOptions.innerHTML = "";
    refreshStock();
  });

  setType("入库");
  loadLocations();

  function setType(t) {
    state.type = t;
    elType1.classList.toggle("on", t === "入库");
    elType2.classList.toggle("on", t === "出库");
    refreshStock();
  }

  function loadLocations() {
    var w = M.AUTH.currentWarehouse();
    if (!w || !w.id) { state.locations = []; return; }
    M.api("GET", "/locations/?warehouse_id=" + w.id).then(function (rows) {
      state.locations = rows || [];
      state.locationsLoaded = true;
    }).catch(function () { state.locations = []; });
  }

  function renderLocOptions() {
    var kw = (elLocation.value || "").trim().toLowerCase();
    if (!kw || !state.locations.length) { elLocOptions.innerHTML = ""; return; }
    var matches = state.locations.filter(function (l) {
      return String(l.location_code || "").toLowerCase().indexOf(kw) !== -1 ||
        String(l.name || "").toLowerCase().indexOf(kw) !== -1;
    }).slice(0, 8);
    elLocOptions.innerHTML = matches.map(function (l) {
      return '<div class="m-item" data-loc-code="' + M.esc(l.location_code) + '" style="padding:8px 0;">' +
        '<div style="font-size:14px;">' + M.esc(l.location_code) +
        (l.name ? ' <span class="m-muted">' + M.esc(l.name) + "</span>" : "") + "</div>" +
        "</div>";
    }).join("");
  }

  function refreshStock() {
    if (!state.goodsBarcode || !state.locationCode) {
      state.currentStock = null;
      elStock.innerHTML = '<span class="m-muted">输入条码与库位后显示当前库存</span>';
      return;
    }
    var w = M.AUTH.currentWarehouse();
    var wh = (w && w.id) ? "?warehouse_id=" + w.id : "";
    M.api("GET", "/stock/" + wh).then(function (rows) {
      var found = (rows || []).filter(function (s) {
        return s.goods_barcode === state.goodsBarcode &&
          s.location_code === state.locationCode;
      });
      var total = found.reduce(function (sum, r) { return sum + Number(r.quantity || 0); }, 0);
      state.currentStock = total;
      elStock.innerHTML = "当前库存：<b class='m-mono'>" + M.fmtNum(total) + "</b>";
    }).catch(function (err) {
      elStock.innerHTML = '<span class="m-muted">库存查询失败</span>';
    });
  }

  window.M_ACTIONS["scanSubmit"] = function () {
    var payload = {
      goods_barcode: state.goodsBarcode,
      location_code: state.locationCode,
      type: state.type,
      quantity: Number(state.quantity || elQty.value || 0),
      remark: elRemark.value.trim()
    };
    if (!payload.goods_barcode || !payload.location_code) { M.toast("请完整填写条码与库位", "err"); return; }
    // 跨仓库防护：后端 /inventory/scan 按"库位所属仓库"执行（只要有该仓权限即可），
    // 并不要求等于 current_warehouse_id——若不做前端校验，页面顶部显示仓库 A、
    // 参考库存按 A 查询，提交却可能实际操作有权限的仓库 B 库位。
    // 因此库位必须属于当前仓库，否则直接阻止提交。
    var curWh = M.AUTH.currentWarehouse();
    if (curWh && curWh.id) {
      if (!state.locationsLoaded) {
        M.toast("库位列表未加载成功，无法校验库位所属仓库，请刷新页面重试", "err");
        return;
      }
      var inCurWh = state.locations.some(function (l) {
        return String(l.location_code || "") === payload.location_code;
      });
      if (!inCurWh) {
        M.toast("该库位不属于当前仓库「" + (curWh.name || String(curWh.id)) + "」，请先切换到该库位所在仓库再操作", "err");
        return;
      }
    }
    if (!(payload.quantity > 0)) { M.toast("数量需大于 0", "err"); return; }
    if (payload.type === "出库" && state.currentStock != null && payload.quantity > state.currentStock) {
      M.toast("出库数量不能大于当前库存", "err"); return;
    }
    var btn = document.getElementById("mScanSubmitBtn");
    btn.disabled = true;
    elResult.innerHTML = "";
    M.api("POST", "/inventory/scan", payload).then(function (res) {
      elResult.innerHTML = '<div class="m-text-ok" style="font-size:14px;line-height:1.6;">' +
        M.esc((res && res.message) || "提交成功") +
        (res && res.goods_name ? "，货物：" + M.esc(res.goods_name) : "") +
        (res && res.location_name ? "，库位：" + M.esc(res.location_name) : "") +
        (res && res.current_stock != null ? "，当前库存：" + M.esc(res.current_stock) : "") +
        "</div>";
      elQty.value = "";
      elRemark.value = "";
      state.quantity = "";
      M.toast("提交成功", "ok");
      refreshStock();
      btn.disabled = false;
    }).catch(function (err) {
      M.toast(err.message || "提交失败", "err");
      btn.disabled = false;
    });
  };

  window.M_ACTIONS["scanFill"] = function (code, el) {
    var where = el.getAttribute("data-scan");
    if (where === "barcode") {
      elBarcode.value = code;
      state.goodsBarcode = code;
      refreshStock();
    } else if (where === "location") {
      elLocation.value = code;
      state.locationCode = code;
      elLocOptions.innerHTML = "";
      refreshStock();
    }
  };
};

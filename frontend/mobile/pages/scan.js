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

  var quick = {
    type: "入库", location: "", rows: {}, scanning: false,
    callbackBusy: false, requestKey: null
  };
  var elQuickTypeIn = document.getElementById("mQuickTypeIn");
  var elQuickTypeOut = document.getElementById("mQuickTypeOut");
  var elQuickLocation = document.getElementById("mQuickLocation");
  var elQuickStart = document.getElementById("mQuickStartBtn");
  var elQuickClear = document.getElementById("mQuickClearBtn");
  var elQuickConfirm = document.getElementById("mQuickConfirmBtn");
  var elQuickList = document.getElementById("mQuickList");
  var elQuickSummary = document.getElementById("mQuickSummary");
  var elQuickResult = document.getElementById("mQuickResult");

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
  elQuickTypeIn.addEventListener("click", function () { setQuickType("入库"); });
  elQuickTypeOut.addEventListener("click", function () { setQuickType("出库"); });
  elQuickLocation.addEventListener("input", function () { quick.location = elQuickLocation.value.trim(); });
  elQuickStart.addEventListener("click", toggleQuickScan);
  elQuickClear.addEventListener("click", clearQuickRows);
  elQuickConfirm.addEventListener("click", confirmQuickScan);
  elQuickList.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-quick-action]") : null;
    if (!btn) return;
    var key = btn.getAttribute("data-key");
    var row = quick.rows[key];
    if (!row) return;
    if (btn.getAttribute("data-quick-action") === "plus") row.quantity += 1;
    if (btn.getAttribute("data-quick-action") === "minus") row.quantity -= 1;
    if (btn.getAttribute("data-quick-action") === "delete" || row.quantity <= 0) delete quick.rows[key];
    renderQuickRows();
  });

  function setQuickType(t) {
    if (quick.scanning) return M.toast("请先停止连续扫码", "err");
    quick.type = t;
    elQuickTypeIn.classList.toggle("on", t === "入库");
    elQuickTypeOut.classList.toggle("on", t === "出库");
    elQuickConfirm.textContent = t === "入库" ? "确认入库" : "确认出库";
  }

  function quickKey(barcode) { return barcode + "|" + quick.location; }
  function newRequestKey() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "h5-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }
  function renderQuickRows() {
    var rows = Object.keys(quick.rows).map(function (key) { return quick.rows[key]; });
    var total = rows.reduce(function (sum, row) { return sum + row.quantity; }, 0);
    elQuickSummary.textContent = rows.length ? ("已扫描 " + total + " 件 · " + rows.length + " 种") : (quick.scanning ? "等待扫码…" : "尚未开始");
    elQuickClear.disabled = !rows.length || quick.scanning;
    elQuickConfirm.disabled = !rows.length || quick.scanning;
    elQuickList.innerHTML = rows.map(function (row) {
      var key = M.esc(quickKey(row.barcode));
      return '<div class="m-item"><div class="m-row between">' +
        '<div class="m-grow"><div class="m-item-title">' + M.esc(row.name || row.barcode) + '</div>' +
        '<div class="m-item-sub m-mono">' + M.esc(row.barcode) + ' · ' + M.esc(quick.location) + '</div></div>' +
        '<div class="m-row" style="gap:6px;"><button type="button" class="m-btn m-btn-ghost m-btn-sm" data-quick-action="minus" data-key="' + key + '">−</button>' +
        '<strong class="m-mono">' + M.esc(row.quantity) + '</strong>' +
        '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-quick-action="plus" data-key="' + key + '">+</button>' +
        '<button type="button" class="m-btn m-btn-danger m-btn-sm" data-quick-action="delete" data-key="' + key + '">删</button></div></div></div>';
    }).join("");
  }
  function clearQuickRows() { if (!quick.scanning) { quick.rows = {}; quick.requestKey = null; elQuickResult.innerHTML = ""; renderQuickRows(); } }
  function feedback(ok, message) {
    if (navigator.vibrate) navigator.vibrate(ok ? 45 : [40, 50, 40]);
    M.toast(message, ok ? "ok" : "err");
  }
  function quickLocationReady() {
    var w = M.AUTH.currentWarehouse();
    if (!w || !w.id) { feedback(false, "请先选择当前仓库"); return false; }
    if (!quick.location) { feedback(false, "请先填写库位"); return false; }
    if (state.locationsLoaded && !state.locations.some(function (l) { return l.location_code === quick.location; })) {
      feedback(false, "库位不属于当前仓库"); return false;
    }
    return true;
  }
  function toggleQuickScan() {
    if (quick.scanning) { quick.scanning = false; quick.callbackBusy = false; elQuickStart.textContent = "继续连续扫码"; renderQuickRows(); return; }
    if (!quickLocationReady()) return;
    quick.scanning = true;
    quick.requestKey = newRequestKey();
    elQuickStart.textContent = "停止扫码";
    renderQuickRows();
    quickScanNext();
  }
  function quickScanNext() {
    if (!quick.scanning || quick.callbackBusy) return;
    quick.callbackBusy = true;
    M.scanCode(function (code) {
      if (!quick.scanning) { quick.callbackBusy = false; return; }
      validateQuickBarcode(String(code || "").trim()).then(function (goods) {
        var key = quickKey(goods.barcode);
        if (!quick.rows[key]) quick.rows[key] = { barcode: goods.barcode, name: goods.name, quantity: 0 };
        quick.rows[key].quantity += 1;
        feedback(true, goods.name || goods.barcode);
        renderQuickRows();
      }).catch(function (err) {
        feedback(false, err.message || ("未找到该货物：" + code));
      }).then(function () {
        quick.callbackBusy = false;
        if (quick.scanning) setTimeout(quickScanNext, 80);
      });
    });
  }
  function validateQuickBarcode(code) {
    if (!code) return Promise.reject(new Error("未读取到条码"));
    return M.api("GET", "/goods/?keyword=" + encodeURIComponent(code)).then(function (rows) {
      var goods = (rows || []).filter(function (g) { return g.barcode === code; })[0];
      if (!goods) throw new Error("未找到该货物：" + code);
      if (quick.type === "出库") {
        var w = M.AUTH.currentWarehouse();
        return M.api("GET", "/stock/?warehouse_id=" + w.id + "&goods_barcode=" + encodeURIComponent(code)).then(function (stocks) {
          var available = (stocks || []).filter(function (s) { return s.location_code === quick.location; }).reduce(function (sum, s) { return sum + Number(s.quantity || 0); }, 0);
          var key = quickKey(code), already = quick.rows[key] ? quick.rows[key].quantity : 0;
          if (available <= already) throw new Error("库存不足：" + goods.name + " 当前可出库 " + available);
          return goods;
        });
      }
      return goods;
    });
  }
  function confirmQuickScan() {
    if (quick.scanning || !quickLocationReady()) return;
    var rows = Object.keys(quick.rows).map(function (key) { return quick.rows[key]; });
    if (!rows.length) return feedback(false, "请先扫码添加货物");
    elQuickConfirm.disabled = true;
    var w = M.AUTH.currentWarehouse();
    var items = rows.map(function (row) { return { goods_barcode: row.barcode, location_code: quick.location, quantity: row.quantity }; });
    M.api("POST", "/inventory/batch", { type: quick.type, items: items }, { headers: { "Idempotency-Key": quick.requestKey || newRequestKey() } })
      .then(function (res) {
        elQuickResult.innerHTML = '<div class="m-text-ok">' + M.esc(res.message || "提交成功") + '：' + M.esc(res.order_no || "") + '</div>';
        feedback(true, "整单提交成功");
        quick.rows = {}; quick.requestKey = null; renderQuickRows();
      }).catch(function (err) { feedback(false, err.message || "提交失败，整单未提交"); elQuickConfirm.disabled = false; });
  }

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
    } else if (where === "quick-location") {
      elQuickLocation.value = code;
      quick.location = code;
      renderQuickRows();
    }
  };
};

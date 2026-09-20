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
    callbackBusy: false, scanner: null, submitting: false, unknown: false,
    requestKey: null, pendingPayload: null,
    lastAcceptedCode: null, lastAcceptedAt: 0, scanGeneration: 0
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
  elQuickLocation.addEventListener("input", function () {
    var next = elQuickLocation.value.trim();
    if ((Object.keys(quick.rows).length || quick.submitting || quick.unknown) && next !== quick.location) {
      elQuickLocation.value = quick.location;
      return feedback(false, quick.unknown ? "提交结果未知，请先用原清单重试" : "已有扫描清单，不能更换库位；请先清空清单");
    }
    quick.location = next;
  });
  elQuickStart.addEventListener("click", toggleQuickScan);
  elQuickClear.addEventListener("click", clearQuickRows);
  elQuickConfirm.addEventListener("click", confirmQuickScan);
  elQuickList.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-quick-action]") : null;
    if (!btn) return;
    var key = btn.getAttribute("data-key");
    var row = quick.rows[key];
    if (!row || quick.submitting || quick.unknown) return;
    if (btn.getAttribute("data-quick-action") === "plus") row.quantity += 1;
    if (btn.getAttribute("data-quick-action") === "minus") row.quantity -= 1;
    if (btn.getAttribute("data-quick-action") === "delete" || row.quantity <= 0) delete quick.rows[key];
    renderQuickRows();
  });

  function setQuickType(t) {
    if (quick.scanning) return M.toast("请先停止连续扫码", "err");
    if (quick.unknown) return M.toast("提交结果未知，请先用原清单重试", "err");
    if (quick.submitting || Object.keys(quick.rows).length) return M.toast("已有扫描清单，请先提交或清空", "err");
    quick.type = t;
    elQuickTypeIn.classList.toggle("on", t === "入库");
    elQuickTypeOut.classList.toggle("on", t === "出库");
    elQuickConfirm.textContent = t === "入库" ? "确认入库" : "确认出库";
  }

  function quickKey(barcode, location) { return barcode + "|" + location; }
  function newRequestKey() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "h5-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }
  function renderQuickRows() {
    var rows = Object.keys(quick.rows).map(function (key) { return quick.rows[key]; });
    var total = rows.reduce(function (sum, row) { return sum + row.quantity; }, 0);
    elQuickSummary.textContent = rows.length ? ("已扫描 " + total + " 件 · " + rows.length + " 种") : (quick.scanning ? "等待扫码…" : "尚未开始");
    elQuickClear.disabled = !rows.length || quick.scanning || quick.submitting || quick.unknown;
    elQuickConfirm.disabled = !rows.length || quick.scanning || quick.submitting;
    elQuickConfirm.textContent = quick.unknown ? "重试原清单" : (quick.type === "入库" ? "确认入库" : "确认出库");
    elQuickStart.disabled = quick.submitting || quick.unknown;
    elQuickLocation.disabled = quick.scanning || quick.submitting || quick.unknown;
    elQuickList.innerHTML = rows.map(function (row) {
      var key = M.esc(quickKey(row.barcode, row.location));
      var disabled = quick.submitting || quick.unknown ? " disabled" : "";
      return '<div class="m-item"><div class="m-row between">' +
        '<div class="m-grow"><div class="m-item-title">' + M.esc(row.name || row.barcode) + '</div>' +
        '<div class="m-item-sub m-mono">' + M.esc(row.barcode) + ' · ' + M.esc(row.location) + '</div></div>' +
        '<div class="m-row" style="gap:6px;"><button type="button" class="m-btn m-btn-ghost m-btn-sm" data-quick-action="minus" data-key="' + key + '"' + disabled + '>−</button>' +
        '<strong class="m-mono">' + M.esc(row.quantity) + '</strong>' +
        '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-quick-action="plus" data-key="' + key + '"' + disabled + '>+</button>' +
        '<button type="button" class="m-btn m-btn-danger m-btn-sm" data-quick-action="delete" data-key="' + key + '"' + disabled + '>删</button></div></div></div>';
    }).join("");
  }
  function clearQuickRows() {
    if (quick.unknown) return feedback(false, "提交结果未知，请先用原清单重试");
    if (!quick.scanning && !quick.submitting) {
      quick.rows = {}; quick.requestKey = null; quick.pendingPayload = null; quick.lastAcceptedCode = null; quick.lastAcceptedAt = 0;
      elQuickResult.innerHTML = ""; renderQuickRows();
    }
  }
  function feedback(ok, message) {
    if (navigator.vibrate) navigator.vibrate(ok ? 45 : [40, 50, 40]);
    M.toast(message, ok ? "ok" : "err");
  }
  function quickLocationReady() {
    var w = M.AUTH.currentWarehouse();
    if (!w || !w.id) { feedback(false, "请先选择当前仓库"); return false; }
    if (quick.location && state.locationsLoaded && !state.locations.some(function (l) { return l.location_code === quick.location; })) {
      feedback(false, "库位不属于当前仓库"); return false;
    }
    return true;
  }
  function toggleQuickScan() {
    if (quick.submitting) return M.toast("正在提交，请稍候", "err");
    if (quick.unknown) return M.toast("提交结果未知，请先用原清单重试", "err");
    if (quick.scanning) {
      quick.scanning = false;
      quick.scanGeneration += 1;
      quick.callbackBusy = false;
      var scanner = quick.scanner;
      quick.scanner = null;
      if (scanner) scanner.cancel();
      elQuickStart.textContent = "继续连续扫码";
      renderQuickRows();
      return;
    }
    if (!quickLocationReady()) return;
    quick.scanGeneration += 1;
    var generation = quick.scanGeneration;
    quick.scanning = true;
    if (!quick.requestKey) quick.requestKey = newRequestKey();
    elQuickStart.textContent = "停止扫码";
    renderQuickRows();
    quickScanNext(generation);
  }
  function quickScanNext(generation) {
    if (!quick.scanning || generation !== quick.scanGeneration || quick.scanner) return;
    quick.scanner = M.scanCode(function (code) {
      if (!quick.scanning || generation !== quick.scanGeneration || quick.submitting || quick.unknown || quick.callbackBusy) return false;
      quick.callbackBusy = true;
      validateQuickBarcode(String(code || "").trim(), quick.location).then(function (resolved) {
        if (!quick.scanning || generation !== quick.scanGeneration || quick.unknown) return;
        var goods = resolved.goods;
        var location = resolved.location;
        var key = quickKey(goods.barcode, location);
        if (!quick.rows[key]) quick.rows[key] = { barcode: goods.barcode, name: goods.name, location: location, quantity: 0 };
        quick.rows[key].quantity += 1;
        feedback(true, goods.name || goods.barcode);
        renderQuickRows();
      }).catch(function (err) {
        if (!quick.scanning || generation !== quick.scanGeneration) return;
        feedback(false, err.message || ("未找到该货物：" + code));
      }).then(function () {
        if (generation !== quick.scanGeneration) return;
        quick.callbackBusy = false;
        renderQuickRows();
      });
      return true;
    }, { continuous: true, onCancel: function () {
      if (generation !== quick.scanGeneration) return;
      quick.scanner = null;
      quick.scanning = false;
      quick.callbackBusy = false;
      elQuickStart.textContent = "继续连续扫码";
      renderQuickRows();
    } });
  }
  function validateQuickBarcode(code, locationOverride) {
    if (!code) return Promise.reject(new Error("未读取到条码"));
    return M.api("GET", "/goods/?keyword=" + encodeURIComponent(code)).then(function (rows) {
      var goods = (rows || []).filter(function (g) { return g.barcode === code; })[0];
      if (!goods) throw new Error("未找到该货物：" + code);
      var w = M.AUTH.currentWarehouse();
      if (!w || !w.id) throw new Error("请先选择当前仓库");
      if (locationOverride) {
        if (quick.type !== "出库") return { goods: goods, location: locationOverride };
        return M.api("GET", "/stock/?warehouse_id=" + w.id + "&goods_barcode=" + encodeURIComponent(code)).then(function (stocks) {
          var matched = (stocks || []).filter(function (s) { return s.location_code === locationOverride; })[0];
          var available = matched ? Number(matched.quantity || 0) : 0;
          var key = quickKey(code, locationOverride), already = quick.rows[key] ? quick.rows[key].quantity : 0;
          if (available <= already) throw new Error("库存不足：" + goods.name + " 在库位 " + locationOverride + " 当前可出库 " + available);
          return { goods: goods, location: locationOverride };
        });
      }
      return M.api("GET", "/stock/?warehouse_id=" + w.id + "&goods_barcode=" + encodeURIComponent(code)).then(function (stocks) {
        var candidates = (stocks || []).map(function (stock) {
          var key = quickKey(code, stock.location_code);
          var already = quick.rows[key] ? quick.rows[key].quantity : 0;
          return { stock: stock, remaining: Number(stock.quantity || 0) - already };
        });
        if (quick.type === "出库") {
          candidates = candidates.filter(function (item) { return item.remaining > 0; }).sort(function (a, b) {
            return b.remaining - a.remaining || Number(a.stock.location_id || 0) - Number(b.stock.location_id || 0);
          });
          if (!candidates.length) throw new Error("库存不足：" + goods.name + "，无法自动确定可出库库位");
          return { goods: goods, location: candidates[0].stock.location_code };
        }
        candidates.sort(function (a, b) {
          return Number(b.stock.quantity || 0) - Number(a.stock.quantity || 0) ||
            Number(a.stock.location_id || 0) - Number(b.stock.location_id || 0);
        });
        if (candidates.length) return { goods: goods, location: candidates[0].stock.location_code };
        var locationsPromise = state.locationsLoaded ? Promise.resolve(state.locations) :
          M.api("GET", "/locations/?warehouse_id=" + w.id).then(function (rows) {
            state.locations = rows || [];
            state.locationsLoaded = true;
            return state.locations;
          });
        return locationsPromise.then(function (locations) {
          locations = (locations || []).filter(function (item) { return item.is_active !== false; });
          if (locations.length === 1) return { goods: goods, location: locations[0].location_code };
          throw new Error("该货物没有现有库存，无法自动确定入库库位，请先填写或扫码库位");
        });
      });
    });
  }
  function confirmQuickScan() {
    if (quick.scanning || quick.submitting) return;
    var requestKey, payload;
    if (quick.unknown) {
      requestKey = quick.requestKey;
      payload = quick.pendingPayload;
      if (!requestKey || !payload) return feedback(false, "原提交信息已丢失，请联系管理员确认库存后再操作");
    } else {
      if (!quickLocationReady()) return;
      var rows = Object.keys(quick.rows).map(function (key) { return quick.rows[key]; });
      if (!rows.length) return feedback(false, "请先扫码添加货物");
      requestKey = quick.requestKey || newRequestKey();
      payload = {
        type: quick.type,
        items: rows.map(function (row) { return { goods_barcode: row.barcode, location_code: row.location, quantity: row.quantity }; })
      };
      quick.requestKey = requestKey;
      quick.pendingPayload = payload;
    }
    quick.submitting = true;
    renderQuickRows();
    M.api("POST", "/inventory/batch", payload, { headers: { "Idempotency-Key": requestKey } })
      .then(function (res) {
        elQuickResult.innerHTML = '<div class="m-text-ok">' + M.esc(res.message || "提交成功") + '：' + M.esc(res.order_no || "") + '</div>';
        feedback(true, "整单提交成功");
        quick.rows = {}; quick.requestKey = null; quick.pendingPayload = null; quick.unknown = false;
        quick.lastAcceptedCode = null; quick.lastAcceptedAt = 0;
      }).catch(function (err) {
        if (err && (err.status == null || err.status >= 500)) {
          quick.unknown = true;
          quick.requestKey = requestKey;
          quick.pendingPayload = payload;
          feedback(false, "提交结果未知，请勿清空或修改，使用原清单重试");
        } else {
          quick.unknown = false;
          quick.requestKey = null;
          quick.pendingPayload = null;
          feedback(false, err.message || "提交失败，整单未提交");
        }
      })
      .then(function () { quick.submitting = false; renderQuickRows(); });
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
      if ((Object.keys(quick.rows).length || quick.submitting || quick.unknown) && code !== quick.location) {
        return feedback(false, "已有扫描清单，不能更换库位；请先清空清单");
      }
      elQuickLocation.value = code;
      quick.location = code;
      renderQuickRows();
    }
  };
};

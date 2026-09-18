/* 申请提交页（对标小程序 pages/apply）：免登录匿名提交 */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["apply"] = function () {
  var M = window.M;
  var state = {
    warehouses: [],
    warehouseId: null,
    goodsItems: [],      // {barcode,name,spec,unit,qty,availableStock,stockUnknown}
    goodsResults: [],
    searchSeq: 0,
    warehouseSeq: 0,
    searchTimer: null,
    submitting: false
  };

  var elWarehouse = document.getElementById("mApplyWarehouse");
  var elApplicant = document.getElementById("mApplyApplicant");
  var elDepartment = document.getElementById("mApplyDepartment");
  var elContact = document.getElementById("mApplyContact");
  var elDescription = document.getElementById("mApplyDescription");
  var elAttachment = document.getElementById("mApplyAttachment");
  var elKw = document.getElementById("mApplyGoodsKw");
  var elResults = document.getElementById("mApplyGoodsResults");
  var elItems = document.getElementById("mApplyGoodsItems");
  var elSearchStatus = document.getElementById("mApplySearchStatus");

  elWarehouse.addEventListener("change", onWarehouseChange);
  elKw.addEventListener("input", onKeywordInput);

  loadWarehouses();

  var elWarehouseErr = document.getElementById("mApplyWarehouseErr");

  function loadWarehouses() {
    elWarehouse.innerHTML = '<option value="">仓库加载中…</option>';
    elWarehouse.disabled = true;
    M.api("GET", "/public/warehouses", null, { auth: false }).then(function (rows) {
      state.warehouses = rows || [];
      var html = '<option value="">请选择申请仓库（必填）</option>';
      state.warehouses.forEach(function (w) {
        html += '<option value="' + M.esc(w.id) + '">' + M.esc(w.name) +
          (w.code ? "（" + M.esc(w.code) + "）" : "") + "</option>";
      });
      elWarehouse.innerHTML = html;
      elWarehouse.disabled = false;
      if (elWarehouseErr) elWarehouseErr.classList.add("m-hidden");
      if (state.warehouses.length === 1) {
        elWarehouse.value = String(state.warehouses[0].id);
        state.warehouseId = state.warehouses[0].id;
      }
    }).catch(function (err) {
      // 失败态：select 禁用占位 + 错误区可点击重试（data-act 委托，见 apply.html）
      var msg = M.esc("仓库加载失败（" + (err && err.message ? err.message : "网络错误") + "）");
      elWarehouse.innerHTML = '<option value="">' + msg + "</option>";
      elWarehouse.disabled = true;
      if (elWarehouseErr) {
        elWarehouseErr.classList.remove("m-hidden");
        elWarehouseErr.textContent = "仓库加载失败，点击此处重试";
      }
    });
  }

  function onWarehouseChange() {
    var id = Number(elWarehouse.value) || null;
    state.warehouseId = id;
    state.warehouseSeq += 1;
    // 切仓同时使在途搜索失效：搜索结果（含可用库存）是仓库维度的
    state.searchSeq += 1;
    if (state.searchTimer) { clearTimeout(state.searchTimer); state.searchTimer = null; }
    state.goodsResults = [];
    elResults.innerHTML = "";
    elSearchStatus.innerHTML = "";
    refreshStocks(state.warehouseSeq);
  }

  /* 切仓批量刷新已选货物库存（与小程序一致：POST /public/stock-lookup）
   * seq 校验：响应回来时若已切过仓（seq 过期），丢弃结果，防止 A 仓旧响应覆盖 B 仓库存 */
  function refreshStocks(seq) {
    var items = state.goodsItems;
    if (!state.warehouseId || !items.length) return;
    if (seq == null) seq = state.warehouseSeq;
    if (seq !== state.warehouseSeq) return;
    var barcodes = [];
    items.forEach(function (g) {
      g.stockUnknown = true;
      if (barcodes.indexOf(g.barcode) === -1) barcodes.push(g.barcode);
    });
    renderItems();
    M.api("POST", "/public/stock-lookup", { warehouse_id: state.warehouseId, barcodes: barcodes }, { auth: false })
      .then(function (rows) {
        if (seq !== state.warehouseSeq) return; // 已切仓，丢弃
        var byCode = {};
        (rows || []).forEach(function (r) { byCode[r.barcode] = Number(r.available_stock) || 0; });
        items.forEach(function (g) {
          g.stockUnknown = !Object.prototype.hasOwnProperty.call(byCode, g.barcode);
          g.availableStock = byCode[g.barcode] || 0;
        });
      })
      .catch(function () {
        if (seq !== state.warehouseSeq) return; // 已切仓，丢弃
        items.forEach(function (g) { g.stockUnknown = true; });
      })
      .then(function () {
        if (seq !== state.warehouseSeq) return; // 已切仓，不再渲染
        renderItems();
      });
  }

  /* 货物搜索：防抖 350ms + 仅接受最新请求（与小程序一致，防限流） */
  function onKeywordInput() {
    var kw = elKw.value.trim();
    state.searchSeq += 1;
    var seq = state.searchSeq;
    state.goodsResults = [];
    elResults.innerHTML = "";
    if (state.searchTimer) clearTimeout(state.searchTimer);
    if (!kw) {
      elSearchStatus.innerHTML = "";
      return;
    }
    elSearchStatus.innerHTML = '<div class="m-hint">搜索中…</div>';
    state.searchTimer = setTimeout(function () { searchGoods(kw, seq); }, 350);
  }

  function searchGoods(kw, seq) {
    M.api("GET", "/public/goods-search?q=" + encodeURIComponent(kw) +
      (state.warehouseId ? "&warehouse_id=" + state.warehouseId : ""), null, { auth: false })
      .then(function (rows) {
        if (seq !== state.searchSeq) return;
        state.goodsResults = rows || [];
        renderResults();
      })
      .catch(function (err) {
        if (seq !== state.searchSeq) return;
        state.goodsResults = [];
        elSearchStatus.innerHTML = '<div class="m-error">' + M.esc(err.message || "搜索失败") + "</div>";
      });
  }

  function renderResults() {
    var rows = state.goodsResults;
    elSearchStatus.innerHTML = rows.length ? "" :
      (elKw.value.trim() ? '<div class="m-hint">未找到匹配货物</div>' : "");
    if (!rows.length) { elResults.innerHTML = ""; return; }
    elResults.innerHTML = rows.map(function (g) {
      var added = state.goodsItems.some(function (it) { return it.barcode === g.barcode; });
      return '<div class="m-item" data-pick-barcode="' + M.esc(g.barcode) + '">' +
        '<div class="m-item-title">' + M.esc(g.name) +
        (added ? ' <span class="m-badge m-badge-done">已添加</span>' : "") + "</div>" +
        '<div class="m-item-sub m-mono">' + M.esc(g.barcode) +
        (g.spec ? " · " + M.esc(g.spec) : "") + (g.unit ? " · " + M.esc(g.unit) : "") + "</div>" +
        '<div class="m-item-sub">可用库存 ' + M.esc(g.available_stock != null ? g.available_stock : "-") + "</div>" +
        "</div>";
    }).join("");
  }

  elResults.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-pick-barcode]") : null;
    if (!t) return;
    pickGoods(t.getAttribute("data-pick-barcode"));
  });

  function pickGoods(barcode) {
    var g = state.goodsResults.find(function (x) { return x.barcode === barcode; });
    if (!g) return;
    if (state.goodsItems.some(function (it) { return it.barcode === barcode; })) {
      M.toast("该货物已在列表中", "err");
      return;
    }
    state.goodsItems.push({
      barcode: g.barcode,
      name: g.name,
      spec: g.spec || "",
      unit: g.unit || "",
      qty: "",
      availableStock: g.available_stock != null ? Number(g.available_stock) : 0,
      stockUnknown: g.available_stock == null
    });
    renderItems();
    elKw.value = "";
    state.goodsResults = [];
    elResults.innerHTML = "";
    elSearchStatus.innerHTML = "";
  }

  function renderItems() {
    var items = state.goodsItems;
    if (!items.length) {
      elItems.innerHTML = '<div class="m-hint">尚未添加货物（选填）</div>';
      return;
    }
    elItems.innerHTML = items.map(function (g, i) {
      var stockTxt = g.stockUnknown ? "库存未知" : "可用 " + M.fmtNum(g.availableStock) + (g.unit ? " " + M.esc(g.unit) : "");
      var stockCls = g.stockUnknown ? "m-text-warn" : (Number(g.availableStock) > 0 ? "m-text-ok" : "m-text-err");
      return '<div class="m-item">' +
        '<div class="m-row between">' +
        '<div class="m-grow"><div class="m-item-title">' + M.esc(g.name) + "</div>" +
        '<div class="m-item-sub m-mono">' + M.esc(g.barcode) + (g.spec ? " · " + M.esc(g.spec) : "") + "</div></div>" +
        '<button type="button" class="m-btn m-btn-danger m-btn-sm" data-act="applyDelItem" data-arg="' + i + '">删除</button>' +
        "</div>" +
        '<div class="m-row m-mt8" style="gap:10px;">' +
        '<div class="m-grow"><input class="m-input m-qty-inp" type="number" inputmode="decimal" min="0" step="any" placeholder="数量" value="' + M.esc(g.qty) + '" data-idx="' + i + '"></div>' +
        '<div class="' + stockCls + ' m-mono" style="font-size:13px;white-space:nowrap;">' + stockTxt + "</div>" +
        "</div>" +
        "</div>";
    }).join("");
  }

  elItems.addEventListener("input", function (e) {
    var t = e.target;
    if (!t.classList.contains("m-qty-inp")) return;
    var idx = Number(t.getAttribute("data-idx"));
    if (state.goodsItems[idx]) state.goodsItems[idx].qty = t.value;
  });

  window.M_ACTIONS["applyDelItem"] = function (el, arg) {
    state.goodsItems.splice(Number(arg), 1);
    renderItems();
  };

  window.M_ACTIONS["applyRetryWarehouse"] = loadWarehouses;

  window.M_ACTIONS["applySubmit"] = function () {
    if (state.submitting) return;
    var applicant = elApplicant.value.trim();
    var department = elDepartment.value.trim();
    var contact = elContact.value.trim();
    var description = elDescription.value.trim();
    var attachmentNote = elAttachment.value.trim();

    if (!state.warehouseId) return M.toast("请选择申请仓库", "err");
    if (!applicant) return M.toast("请填写申请人", "err");
    if (!contact) return M.toast("请填写联系邮箱", "err");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact)) return M.toast("联系邮箱格式不正确", "err");
    if (!description) return M.toast("请填写事由描述", "err");

    var items = state.goodsItems.filter(function (g) { return (g.qty || "").trim() !== ""; });
    items.forEach(function (g) {
      if (!(Number(g.qty) > 0)) throw new Error("货物「" + g.name + "」数量需大于 0");
    });

    var payload = {
      applicant_name: applicant,
      contact: contact,
      description: description,
      warehouse_id: state.warehouseId,
      items: items.map(function (g) { return { barcode: g.barcode, quantity: Number(g.qty) }; })
    };
    if (department) payload.department = department;
    if (attachmentNote) payload.attachment_note = attachmentNote;

    state.submitting = true;
    var btn = document.getElementById("mApplySubmitBtn");
    btn.disabled = true;
    btn.textContent = "提交中…";

    M.api("POST", "/requests/", payload, { auth: false }).then(function (res) {
      var ref = (res && (res.reference || res.request_no)) || "";
      M.modal({
        title: "提交成功",
        bodyHtml:
          '<div style="text-align:center;">请保存申请编号，处理进展请联系受理管理员跟进。</div>' +
          '<div class="m-ref-code">' + M.esc(ref || "-") + "</div>",
        buttons: [
          { label: "复制编号", kind: "ghost", onClick: function (close) {
              copyText(ref, function (ok2) { M.toast(ok2 ? "已复制" : "复制失败，请手动记录", ok2 ? "ok" : "err"); });
            } },
          { label: "完成", kind: "primary", onClick: function (close) { close(); resetForm(); } }
        ]
      });
      state.submitting = false;
      btn.disabled = false;
      btn.textContent = "提交申请";
    }).catch(function (err) {
      state.submitting = false;
      btn.disabled = false;
      btn.textContent = "提交申请";
      M.toast(err.message || "提交失败", "err");
    });
  };

  function resetForm() {
    elApplicant.value = "";
    elDepartment.value = "";
    elContact.value = "";
    elDescription.value = "";
    elAttachment.value = "";
    elKw.value = "";
    state.goodsItems = [];
    state.goodsResults = [];
    renderItems();
    elResults.innerHTML = "";
    elSearchStatus.innerHTML = "";
  }

  function copyText(text, cb) {
    if (!text) { cb(false); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { cb(true); }, function () { cb(fallbackCopy(text)); });
    } else {
      cb(fallbackCopy(text));
    }
  }
  function fallbackCopy(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }
};

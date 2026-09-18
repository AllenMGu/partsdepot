/* 盘点单页（对标小程序 pages/check-order + scan 页盘点模式） */
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

window.M_PAGES["check"] = function () {
  var M = window.M;
  if (!M.AUTH.require()) return;

  var elRemark = document.getElementById("mCheckRemark");
  var elOrders = document.getElementById("mCheckOrders");
  var elDetail = document.getElementById("mCheckDetail");
  var currentHeader = null;
  var locations = [];

  /* in-flight guard：手机双击/连点防护（与 orders_common 同一模式）——
   * 任一 mutation（创建/加明细/完成）在途时拒绝新的 mutation，
   * 防止连点创建两张盘点单、重复写入盘点记录（GET 刷新不受限） */
  var inFlight = 0;
  function guarded(fn) {
    if (inFlight > 0) { M.toast("操作处理中，请勿重复操作", "err"); return; }
    inFlight += 1;
    fn(function () { inFlight = Math.max(0, inFlight - 1); });
  }

  loadOrders();
  loadLocations();

  function loadLocations() {
    var w = M.AUTH.currentWarehouse();
    if (!w || !w.id) { locations = []; return; }
    M.api("GET", "/locations/?warehouse_id=" + w.id).then(function (rows) {
      locations = rows || [];
    }).catch(function () { locations = []; });
  }

  function renderLocOptions() {
    var box = document.getElementById("mChkLocOptions");
    if (!box) return;
    var kw = (document.getElementById("mChkLocation").value || "").trim().toLowerCase();
    if (!kw || !locations.length) { box.innerHTML = ""; return; }
    var matches = locations.filter(function (l) {
      return String(l.location_code || "").toLowerCase().indexOf(kw) !== -1 ||
        String(l.name || "").toLowerCase().indexOf(kw) !== -1;
    }).slice(0, 8);
    box.innerHTML = matches.map(function (l) {
      return '<div class="m-item" data-loc-code="' + M.esc(l.location_code) + '" style="padding:8px 0;">' +
        '<div style="font-size:14px;">' + M.esc(l.location_code) +
        (l.name ? ' <span class="m-muted">' + M.esc(l.name) + "</span>" : "") + "</div>" +
        "</div>";
    }).join("");
  }

  function onLocOptionsClick(e) {
    var box = document.getElementById("mChkLocOptions");
    var t = e.target.closest ? e.target.closest("[data-loc-code]") : null;
    if (!t || !box) return;
    document.getElementById("mChkLocation").value = t.getAttribute("data-loc-code");
    box.innerHTML = "";
  }

  function loadOrders() {
    elOrders.innerHTML = '<div class="m-empty">加载中…</div>';
    // 按当前仓库查询（与页面顶部仓库上下文一致）
    var w = M.AUTH.currentWarehouse();
    var wh = (w && w.id) ? "?warehouse_id=" + w.id : "";
    M.api("GET", "/check-orders/" + wh).then(function (res) {
      var list = ((res && res.data) || res || []);
      if (!Array.isArray(list)) list = [];
      if (!list.length) {
        elOrders.innerHTML = '<div class="m-empty">暂无盘点单</div>';
        return;
      }
      elOrders.innerHTML = list.map(function (o) {
        return '<div class="m-item">' +
          '<div class="m-row between">' +
          '<div class="m-item-title m-mono">' + M.esc(o.order_no) + "</div>" +
          M.statusBadge(o.status) +
          "</div>" +
          '<div class="m-item-sub m-mt8">' +
          M.esc(o.warehouse_name || "") +
          (o.item_count != null ? " · " + o.item_count + " 项明细" : "") +
          " · " + M.fmtDT(o.create_time) +
          "</div>" +
          '<div class="m-row m-mt8">' +
          '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-act="chkOpen" data-arg="' + o.id + '">查看/盘点</button>' +
          "</div>" +
          "</div>";
      }).join("");
    }).catch(function (err) {
      elOrders.innerHTML = '<div class="m-empty">' + M.esc(err.message || "加载失败") + "</div>";
    });
  }

  function openDetail(id) {
    M.api("GET", "/check-orders/" + id).then(function (res) {
      currentHeader = res.header || {};
      var items = (res.items || []).map(function (it) {
        var diff = Number(it.diff_quantity || 0);
        return Object.assign({}, it, {
          check_quantity: M.fmtNum(it.check_quantity),
          actual_quantity: M.fmtNum(it.actual_quantity),
          diff_quantity: M.fmtNum(it.diff_quantity),
          diffOk: Math.abs(diff) < 0.01
        });
      });
      elDetail.innerHTML =
        '<div class="m-row between" style="margin-bottom:10px;">' +
        '<div class="m-item-title">当前盘点单：<span class="m-mono">' + M.esc(currentHeader.order_no) + "</span></div>" +
        M.statusBadge(currentHeader.status) +
        "</div>" +
        (currentHeader.status !== "COMPLETED"
          ? '<div class="m-card" style="background:#f8fafc;">' +
            '<div class="m-field"><div class="m-label">货物条码</div>' +
            '<div class="m-row"><input class="m-input m-grow" id="mChkBarcode" placeholder="扫描或输入条码">' +
            '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-scan="chkBarcode">扫码</button></div></div>' +
            '<div class="m-field"><div class="m-label">库位编码</div>' +
            '<div class="m-row"><input class="m-input m-grow" id="mChkLocation" placeholder="扫描或输入库位">' +
            '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-scan="chkLocation">扫码</button></div>' +
            '<div id="mChkLocOptions" class="m-mt8" style="border-top:1px solid var(--m-line);"></div></div>' +
            '<div class="m-field"><div class="m-label">盘点数量</div>' +
            '<input class="m-input" id="mChkQty" type="number" inputmode="decimal" min="0" step="any" placeholder="实盘数量"></div>' +
            '<div class="m-btn-row">' +
            '<button type="button" class="m-btn m-btn-ghost" data-act="chkClose">返回</button>' +
            '<button type="button" class="m-btn m-btn-ghost" data-act="chkComplete">完成盘点</button>' +
            '<button type="button" class="m-btn m-btn-primary" data-act="chkAddItem">确认数量</button>' +
            "</div>" +
            "</div>"
          : "") +
        '<div class="m-card-title m-mt12">盘点明细（' + items.length + '）</div>' +
        '<div>' + items.map(function (it) {
          return '<div class="m-item">' +
            '<div class="m-row between">' +
            '<div class="m-grow"><div style="font-size:14px;font-weight:500;">' + M.esc(it.goods_name || it.goods_barcode) + "</div>" +
            '<div class="m-item-sub m-mono">' + M.esc(it.goods_barcode || "") +
            (it.location_code ? " · " + M.esc(it.location_code) : "") + "</div></div>" +
            '<div class="m-badge ' + (it.diffOk ? "m-badge-done" : "m-badge-warn") + '">' +
            (it.diffOk ? "一致" : "差异 " + it.diff_quantity) + "</div>" +
            "</div>" +
            '<div class="m-item-sub m-mt8">系统 ' + it.actual_quantity + " / 盘点 " + it.check_quantity + "</div>" +
            "</div>";
        }).join("") + (items.length ? "" : '<div class="m-empty">暂无盘点明细</div>') + "</div>";
      // 接入库位联想
      var locInput = document.getElementById("mChkLocation");
      if (locInput) {
        locInput.addEventListener("input", renderLocOptions);
        var box = document.getElementById("mChkLocOptions");
        if (box) box.addEventListener("click", onLocOptionsClick);
      }
    }).catch(function (err) { M.toast(err.message || "加载失败", "err"); });
  }

  window.M_ACTIONS["chkOpen"] = function (el, arg) {
    elDetail.classList.remove("m-hidden");
    openDetail(Number(arg));
    setTimeout(function () { elDetail.scrollIntoView({ behavior: "smooth" }); }, 60);
  };
  window.M_ACTIONS["chkClose"] = function () {
    elDetail.innerHTML = "";
    elDetail.classList.add("m-hidden");
    currentHeader = null;
  };

  window.M_ACTIONS["chkCreate"] = function () {
    var remark = elRemark.value.trim();
    var w = M.AUTH.currentWarehouse();
    guarded(function (release) {
      M.api("POST", "/check-orders/", {
        warehouse_id: (w && w.id) ? w.id : null,
        remark: remark || null
      })
        .then(function (created) {
          release();
          M.toast("盘点单已创建", "ok");
          elRemark.value = "";
          loadOrders();
          if (created && created.id) {
            elDetail.classList.remove("m-hidden");
            openDetail(created.id);
          }
        })
        .catch(function (err) { release(); M.toast(err.message || "创建失败", "err"); });
    });
  };

  window.M_ACTIONS["chkAddItem"] = function () {
    if (!currentHeader || !currentHeader.id) { M.toast("请先选择盘点单", "err"); return; }
    var barcode = document.getElementById("mChkBarcode").value.trim();
    var location = document.getElementById("mChkLocation").value.trim();
    var qty = Number(document.getElementById("mChkQty").value);
    if (!barcode || !location || isNaN(qty) || qty < 0) { M.toast("请完整填写盘点信息", "err"); return; }
    // 跨仓库防护（与扫码页同一类问题）：后端按"库位所属仓库"执行盘点，
    // 若库位不属于当前仓库，页面上下文（顶部仓库/库存参考）与实际执行仓库不一致，阻止提交
    var curWh = M.AUTH.currentWarehouse();
    if (curWh && curWh.id) {
      var inCurWh = locations.some(function (l) {
        return String(l.location_code || "") === location;
      });
      if (!inCurWh) {
        M.toast("该库位不属于当前仓库「" + (curWh.name || String(curWh.id)) + "」，无法加入本盘点单", "err");
        return;
      }
    }
    guarded(function (release) {
    M.api("POST", "/check-orders/items/", {
      header_id: currentHeader.id,
      goods_barcode: barcode,
      location_code: location,
      check_quantity: qty
    }).then(function (res) {
      release();
      var diff = Number((res && res.diff_quantity) || 0);
      var msg = Math.abs(diff) < 0.01 ? "盘点一致" : "盘点差异: " + M.fmtNum(diff);
      M.modal({
        title: msg,
        body: "是否继续盘点下一项？",
        buttons: [
          { label: "继续", kind: "primary", onClick: function (close) {
              close();
              document.getElementById("mChkBarcode").value = "";
              document.getElementById("mChkLocation").value = "";
              document.getElementById("mChkQty").value = "";
              var b = document.getElementById("mChkBarcode");
              if (b) b.focus();
            } },
          { label: "完成盘点", kind: "ghost", onClick: function (close) {
              close();
              window.M_ACTIONS["chkComplete"]();
            } }
        ]
      });
      openDetail(currentHeader.id);
      loadOrders();
    }).catch(function (err) { release(); M.toast(err.message || "保存失败", "err"); });
    });
  };

  window.M_ACTIONS["chkComplete"] = function () {
    if (!currentHeader || !currentHeader.id) { M.toast("请先选择盘点单", "err"); return; }
    guarded(function (release) {
      M.api("POST", "/check-orders/" + currentHeader.id + "/complete")
        .then(function (res) {
          release();
          M.toast((res && res.message) || "盘点完成", "ok");
          openDetail(currentHeader.id);
          loadOrders();
        })
        .catch(function (err) { release(); M.toast(err.message || "完成失败", "err"); });
    });
  };

  window.M_ACTIONS["chkRefresh"] = loadOrders;

  window.M_ACTIONS["scanFill"] = function (code, el) {
    var where = el.getAttribute("data-scan");
    if (where === "chkBarcode") { var b = document.getElementById("mChkBarcode"); if (b) b.value = code; }
    else if (where === "chkLocation") { var l = document.getElementById("mChkLocation"); if (l) l.value = code; }
  };
};

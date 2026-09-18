/* 入库单 / 出库单（对标小程序 pages/inbound、pages/outbound）
 * 两页共用本工厂：inbound.js / outbound.js 传入配置即可。 */
(function () {
"use strict";
window.M_PAGES = window.M_PAGES || {};
window.M_ACTIONS = window.M_ACTIONS || {};

function createOrderPage(cfg) {
  // cfg: {key:'inbound'|'outbound', partnerLabel:'供应商'|'客户', partnerField:'supplier'|'customer', hasReturn:bool}
  return function init() {
    var M = window.M;
    if (!M.AUTH.require()) return;

    var elOrders = document.getElementById("mOrderList");
    var elCount = document.getElementById("mOrderCount");
    var elPartner = document.getElementById("mCreatePartner");
    var elRemark = document.getElementById("mCreateRemark");
    var elDetail = document.getElementById("mOrderDetail");

    var base = cfg.apiBase; // '/inbound-orders' 或 '/outbound-orders'

    loadOrders();

    function loadOrders() {
      elOrders.innerHTML = '<div class="m-empty">加载中…</div>';
      M.api("GET", base + "/").then(function (rows) {
        var list = rows || [];
        elCount.textContent = "共 " + list.length + " 单";
        if (!list.length) {
          elOrders.innerHTML = '<div class="m-empty">暂无单据</div>';
          return;
        }
        elOrders.innerHTML = list.map(function (o) {
          return '<div class="m-item">' +
            '<div class="m-row between">' +
            '<div class="m-item-title m-mono">' + M.esc(o.order_no) + "</div>" +
            M.statusBadge(o.status) +
            "</div>" +
            '<div class="m-item-sub m-mt8">' +
            M.esc(cfg.partnerLabel + "：" + (o[cfg.partnerField] || "-")) +
            " · " + (o.item_count != null ? o.item_count + " 项明细" : "") +
            (o.total_amount != null ? " · 金额 " + M.fmtNum(o.total_amount) : "") +
            "</div>" +
            '<div class="m-item-sub m-mt8">' + M.fmtDT(o.create_time) + "</div>" +
            '<div class="m-row m-mt8" style="gap:8px;">' +
            '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-act="ordOpen" data-arg="' + o.id + '">查看/编辑</button>' +
            (o.status === "DRAFT"
              ? '<button type="button" class="m-btn m-btn-primary m-btn-sm" data-act="ordSubmit" data-arg="' + o.id + '">提交</button>'
              : (cfg.hasReturn && o.status === "COMPLETED"
                ? '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-act="ordReturn" data-arg="' + o.id + '">退库</button>'
                : "")) +
            "</div>" +
            "</div>";
        }).join("");
      }).catch(function (err) {
        elOrders.innerHTML = '<div class="m-empty">' + M.esc(err.message || "加载失败") + "</div>";
      });
    }

    function openDetail(id) {
      M.api("GET", base + "/" + id).then(function (d) {
        var items = (d.items || []).map(function (it) {
          return Object.assign({}, it, {
            quantity: M.fmtNum(it.quantity),
            unit_price: M.fmtNum(it.unit_price),
            total_price: M.fmtNum(it.total_price)
          });
        });
        elDetail.innerHTML =
          '<div class="m-row between" style="margin-bottom:10px;">' +
          '<div class="m-item-title m-mono">' + M.esc(d.order_no) + "</div>" +
          M.statusBadge(d.status) +
          "</div>" +
          '<div class="m-item-sub" style="margin-bottom:12px;">' +
          M.esc(cfg.partnerLabel + "：" + (d[cfg.partnerField] || "-")) +
          (d.total_amount != null ? " · 金额 " + M.fmtNum(d.total_amount) : "") +
          " · " + M.fmtDT(d.create_time) +
          (d.remark ? " · " + M.esc(d.remark) : "") +
          "</div>" +
          (d.status === "DRAFT"
            ? '<div class="m-card" style="background:#f8fafc;">' +
              '<div class="m-card-title">添加明细</div>' +
              '<div class="m-field"><div class="m-label">货物条码</div>' +
              '<div class="m-row"><input class="m-input m-grow" id="mItBarcode" placeholder="扫描或输入条码">' +
              '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-scan="barcode">扫码</button></div></div>' +
              '<div class="m-field"><div class="m-label">库位编码</div>' +
              '<div class="m-row"><input class="m-input m-grow" id="mItLocation" placeholder="扫描或输入库位">' +
              '<button type="button" class="m-btn m-btn-ghost m-btn-sm" data-scan="location">扫码</button></div></div>' +
              '<div class="m-row" style="gap:8px;">' +
              '<div class="m-field m-grow"><div class="m-label">数量</div><input class="m-input" id="mItQty" type="number" inputmode="decimal" min="0" step="any" placeholder="数量"></div>' +
              '<div class="m-field m-grow"><div class="m-label">单价(选填)</div><input class="m-input" id="mItPrice" type="number" inputmode="decimal" min="0" step="any" placeholder="0"></div>' +
              "</div>" +
              '<div class="m-field"><div class="m-label">备注(选填)</div><input class="m-input" id="mItRemark" placeholder="备注"></div>' +
              '<button type="button" class="m-btn m-btn-primary" data-act="ordAddItem" data-arg="' + d.id + '">添加明细</button>' +
              "</div>"
            : "") +
          '<div class="m-card-title m-mt12">明细（' + items.length + '）</div>' +
          '<div id="mItList">' + items.map(function (it) {
            return '<div class="m-item">' +
              '<div class="m-row between">' +
              '<div class="m-grow"><div style="font-size:14px;font-weight:500;">' + M.esc(it.goods_name || it.goods_barcode) + "</div>" +
              '<div class="m-item-sub m-mono">' + M.esc(it.goods_barcode || "") +
              (it.location_code ? " · " + M.esc(it.location_code) : "") + "</div></div>" +
              '<div class="m-mono" style="font-size:14px;">' + it.quantity +
              (it.unit_price !== "0.00" && it.unit_price != null ? " × " + it.unit_price : "") + "</div>" +
              "</div>" +
              (d.status === "DRAFT" && it.id
                ? '<div class="m-row m-mt8"><button type="button" class="m-btn m-btn-danger m-btn-sm" data-act="ordDelItem" data-arg="' + d.id + "," + it.id + '">删除明细</button></div>'
                : "") +
              (it.remark ? '<div class="m-item-sub m-mt8">' + M.esc(it.remark) + "</div>" : "") +
              "</div>";
          }).join("") + (items.length ? "" : '<div class="m-empty">暂无明细</div>') + "</div>" +
          '<div class="m-btn-row m-mt12">' +
          (d.status === "DRAFT"
            ? '<button type="button" class="m-btn m-btn-ghost" data-act="ordCloseDetail">关闭</button>' +
              '<button type="button" class="m-btn m-btn-danger" data-act="ordDelOrder" data-arg="' + d.id + '">删除单据</button>' +
              '<button type="button" class="m-btn m-btn-primary" data-act="ordSubmit" data-arg="' + d.id + '">提交</button>'
            : (cfg.hasReturn && d.status === "COMPLETED"
              ? '<button type="button" class="m-btn m-btn-ghost" data-act="ordCloseDetail">关闭</button>' +
                '<button type="button" class="m-btn m-btn-primary" data-act="ordReturn" data-arg="' + d.id + '">退库</button>'
              : '<button type="button" class="m-btn m-btn-ghost" data-act="ordCloseDetail">关闭</button>')) +
          "</div>";
      }).catch(function (err) { M.toast(err.message || "加载失败", "err"); });
    }

    window.M_ACTIONS["ordOpen"] = function (el, arg) {
      elDetail.classList.remove("m-hidden");
      openDetail(Number(arg));
      setTimeout(function () { elDetail.scrollIntoView({ behavior: "smooth" }); }, 60);
    };
    window.M_ACTIONS["ordCloseDetail"] = function () {
      elDetail.innerHTML = "";
      elDetail.classList.add("m-hidden");
    };

    window.M_ACTIONS["ordCreate"] = function () {
      var partner = elPartner.value.trim();
      var remark = elRemark.value.trim();
      M.api("POST", base + "/", { [cfg.partnerField]: partner, remark: remark })
        .then(function () {
          M.toast("创建成功", "ok");
          elPartner.value = "";
          elRemark.value = "";
          loadOrders();
        })
        .catch(function (err) { M.toast(err.message || "创建失败", "err"); });
    };

    window.M_ACTIONS["ordAddItem"] = function (el, arg) {
      var headerId = Number(arg);
      var barcode = document.getElementById("mItBarcode").value.trim();
      var location = document.getElementById("mItLocation").value.trim();
      var qty = Number(document.getElementById("mItQty").value || 0);
      var priceRaw = document.getElementById("mItPrice").value.trim();
      var remark = document.getElementById("mItRemark").value.trim();
      if (!barcode || !location || !(qty > 0)) { M.toast("请完整填写条码/库位/数量", "err"); return; }
      var body = {
        goods_barcode: barcode,
        location_code: location,
        quantity: qty,
        unit_price: priceRaw === "" ? null : Number(priceRaw),
        remark: remark
      };
      M.api("POST", base + "/" + headerId + "/items", body)
        .then(function () { M.toast("已添加", "ok"); loadOrders(); openDetail(headerId); })
        .catch(function (err) { M.toast(err.message || "添加失败", "err"); });
    };

    window.M_ACTIONS["ordDelItem"] = function (el, arg) {
      var parts = arg.split(",");
      var headerId = Number(parts[0]);
      var itemId = Number(parts[1]);
      M.modal({
        title: "删除明细",
        body: "确认删除该明细行？",
        buttons: [
          { label: "取消", kind: "ghost" },
          { label: "删除", kind: "danger", onClick: function (close) {
              close();
              M.api("DELETE", base + "/" + headerId + "/items/" + itemId)
                .then(function () { M.toast("已删除", "ok"); loadOrders(); openDetail(headerId); })
                .catch(function (err) { M.toast(err.message || "删除失败", "err"); });
            } }
        ]
      });
    };

    window.M_ACTIONS["ordSubmit"] = function (el, arg) {
      var id = Number(arg);
      M.api("POST", base + "/" + id + "/submit")
        .then(function () { M.toast("提交成功", "ok"); loadOrders(); elDetail.innerHTML = ""; elDetail.classList.add("m-hidden"); })
        .catch(function (err) { M.toast(err.message || "提交失败", "err"); });
    };

    window.M_ACTIONS["ordDelOrder"] = function (el, arg) {
      var id = Number(arg);
      M.modal({
        title: "删除单据",
        body: "确认删除该单据？删除后不可恢复。",
        buttons: [
          { label: "取消", kind: "ghost" },
          { label: "删除", kind: "danger", onClick: function (close) {
              close();
              M.api("DELETE", base + "/" + id)
                .then(function () { M.toast("已删除", "ok"); elDetail.innerHTML = ""; elDetail.classList.add("m-hidden"); loadOrders(); })
                .catch(function (err) { M.toast(err.message || "删除失败", "err"); });
            } }
        ]
      });
    };

    if (cfg.hasReturn) {
      window.M_ACTIONS["ordReturn"] = function (el, arg) {
        var id = Number(arg);
        M.api("POST", base + "/" + id + "/return")
          .then(function (res) { M.toast("已生成 " + ((res && res.order_no) || "出库单"), "ok"); loadOrders(); })
          .catch(function (err) { M.toast(err.message || "退库失败", "err"); });
      };
    }

    window.M_ACTIONS["ordRefresh"] = loadOrders;

    // 扫码填充
    window.M_ACTIONS["scanFill"] = function (code, el) {
      var where = el.getAttribute("data-scan");
      if (where === "barcode") { var b = document.getElementById("mItBarcode"); if (b) b.value = code; }
      else if (where === "location") { var l = document.getElementById("mItLocation"); if (l) l.value = code; }
    };
  };
}

window.createWmsOrderPage = createOrderPage;
})();

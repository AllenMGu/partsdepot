/* 小程序申请页并发行为单元测试：使用最小运行时加载 Page 定义，不依赖微信开发者工具。 */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

let page;
let resolveLookup;
const context = {
  require: () => ({ request: () => new Promise((resolve) => { resolveLookup = resolve; }) }),
  Page: (definition) => { page = definition; },
  wx: { showToast: () => {} },
  setTimeout,
  clearTimeout
};
vm.runInNewContext(
  fs.readFileSync("wechat-miniprogram/pages/apply/index.js", "utf8"),
  context,
  { filename: "wechat-miniprogram/pages/apply/index.js" }
);
page.setData = function setData(patch, callback) {
  Object.assign(this.data, patch);
  if (callback) callback();
};

async function testLateBatchResponseDoesNotOverwriteNewRow() {
  page.data.warehouseId = 2;
  page._warehouseSeq = 1;
  page.data.goodsItems = [{ barcode: "A", name: "A", availableStock: 12, stockUnknown: false }];

  const pending = page.refreshPickedStock(1);
  page.onPickGoods({
    currentTarget: { dataset: { goods: { barcode: "B", name: "B", available_stock: 8 } } }
  });
  resolveLookup([{ barcode: "A", available_stock: 3 }]);
  await pending;

  const a = page.data.goodsItems.find((item) => item.barcode === "A");
  const b = page.data.goodsItems.find((item) => item.barcode === "B");
  assert.strictEqual(a.availableStock, 3);
  assert.strictEqual(a.stockUnknown, false);
  assert.strictEqual(b.availableStock, 8);
  assert.strictEqual(b.stockUnknown, false);
}

testLateBatchResponseDoesNotOverwriteNewRow()
  .then(() => console.log("PASS | 小程序旧批量响应不覆盖请求期间新增行"))
  .catch((error) => { console.error(error); process.exit(1); });

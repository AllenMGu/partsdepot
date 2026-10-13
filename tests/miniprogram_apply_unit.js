/* 小程序申请页并发行为单元测试：使用最小运行时加载 Page 定义，不依赖微信开发者工具。 */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

let page;
let resolveLookup;
let lastRequest;
const context = {
  require: () => ({ request: (options) => {
    lastRequest = options;
    return new Promise((resolve) => { resolveLookup = resolve; });
  } }),
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

async function testDepartmentAndSubmitWithoutEmail() {
  page.resetForm();
  assert.strictEqual(page.data.departmentIndex, -1);
  assert.deepStrictEqual(Array.from(page.data.departments),
    ["行政事务部", "工程管理部", "安全管理部", "生产技术部", "质量管理", "综合管理部"]);
  page.onDepartmentChange({ detail: { value: "4" } });
  assert.strictEqual(page.data.department, "质量管理");
  Object.assign(page.data, { applicantName: "申请人", description: "测试用途", warehouseId: 2 });
  assert.strictEqual(page.validate(), "");
  const pending = page.submit();
  assert.strictEqual(lastRequest.data.department, "质量管理");
  assert.strictEqual(Object.hasOwn(lastRequest.data, "contact"), false);
  resolveLookup({ reference: "APP-TEST" });
  context.wx.showModal = () => {};
  await pending;
  page.resetForm();
  assert.strictEqual(page.data.department, "");
  assert.strictEqual(page.data.departmentIndex, -1);
  assert.strictEqual(page.data.submitting, false);
}

testLateBatchResponseDoesNotOverwriteNewRow()
  .then(testDepartmentAndSubmitWithoutEmail)
  .then(() => console.log("PASS | 小程序库存响应、部门选择、无邮箱提交及重置"))
  .catch((error) => { console.error(error); process.exit(1); });

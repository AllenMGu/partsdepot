const assert = require('assert');

let role = 'viewer';
const actions = [];
global.wx = {
  getStorageSync(key) {
    if (key === 'wms_access_token') return 'test-token';
    if (key === 'wms_user') return { id: 1, role };
    return '';
  },
  switchTab(opts) { actions.push(opts.url); },
  navigateTo(opts) { actions.push(opts.url); },
  showToast() {},
  reLaunch(opts) { actions.push(opts.url); },
};
let page;
global.Page = config => { page = config; };

const guard = require('../wechat-miniprogram/utils/guard');
assert.strictEqual(guard.requireOperator(), false);
assert.deepStrictEqual(actions, ['/pages/stock/index']);
role = 'operator';
assert.strictEqual(guard.requireOperator(), true);

require('../wechat-miniprogram/pages/home/index');
async function homeFor(userRole) {
  role = userRole;
  const instance = Object.assign({}, page, {
    data: {},
    setData(updates) { Object.assign(this.data, updates); },
    loadOverview: async () => {},
  });
  await instance.onShow();
  return instance;
}

(async () => {
  const viewer = await homeFor('viewer');
  assert.strictEqual(viewer.data.canOperate, false);
  const count = actions.length;
  viewer.goScan();
  viewer.goOrders();
  assert.strictEqual(actions.length, count);
  const operator = await homeFor('operator');
  assert.strictEqual(operator.data.canOperate, true);
  operator.goScan();
  assert.strictEqual(actions.at(-1), '/pages/scan/index');
  console.log('PASS | 小程序只读账号隐藏并拦截仓库操作入口');
})().catch(error => { console.error(error); process.exitCode = 1; });

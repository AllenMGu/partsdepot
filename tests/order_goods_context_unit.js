// Run the real inline order-page functions with two warehouses sharing one barcode.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

async function checkPage(page) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'frontend', page + '.html'), 'utf8');
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).filter(source => source.trim());
  assert.strictEqual(scripts.length, 1);
  const urls = [];
  const ctx = {
    API_BASE_URL: 'api', encodeURIComponent,
    getHeaders: () => ({}), console: { log() {}, error() {} },
    document: { getElementById: () => ({ classList: { add() {} } }) },
    fetch: async url => {
      urls.push(url);
      if (url.startsWith('api/goods/')) return { ok: true, json: async () => [
        { barcode: 'SAME', warehouse_id: 2, name: '行政用品', price: 8 },
      ] };
      if (url.startsWith('api/stock/')) return { ok: true, json: async () => [
        { warehouse_id: 1, goods_barcode: 'SAME', quantity: 5 },
        { warehouse_id: 2, goods_barcode: 'SAME', quantity: 8 },
      ] };
      throw Error('Unexpected request: ' + url);
    },
  };
  vm.createContext(ctx);
  vm.runInContext(scripts[0], ctx, { filename: page + '.html' });
  vm.runInContext(`currentOrderWarehouseId = 2;
    goodsCache.set(goodsCacheKey(1, 'SAME'),
      {barcode: 'SAME', warehouse_id: 1, name: '工程耗材', price: 3});
    showGoodsInfo = async goods => { displayedGoods = goods; };`, ctx);

  await vm.runInContext("validateGoods('SAME')", ctx);
  assert.strictEqual(vm.runInContext('displayedGoods.name', ctx), '行政用品');
  assert(urls[0].includes('warehouse_id=2'), urls[0]);
  const before = urls.length;
  await vm.runInContext("validateGoods('SAME')", ctx);
  assert.strictEqual(urls.length, before, 'second lookup should reuse B warehouse cache');
  assert.strictEqual(await vm.runInContext("getAvailableStock('SAME')", ctx), 8);
  assert(urls.at(-1).includes('warehouse_id=2'), urls.at(-1));
  console.log('PASS | ' + page + ' 同条码按单据仓库选货、缓存及计算库存');
}

(async () => {
  await checkPage('inbound');
  await checkPage('outbound');
})().catch(error => { console.error(error); process.exitCode = 1; });

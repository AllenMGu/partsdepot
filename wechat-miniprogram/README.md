# WMS 微信小程序（基于 `main.py`）

这是一个可直接导入微信开发者工具的前端小程序，已对接你当前后端：

- API 基础地址：`https://HOSTS/api`
- 登录接口：`POST /token`（`application/x-www-form-urlencoded`）
- 已接入功能：
  - 登录
  - 首页概览
  - 库存列表
  - 扫码出入库
  - 扫码盘点
  - 入库单完整流程（创建、加明细、提交、删除、退库）
  - 出库单完整流程（创建、加明细、提交、删除）
  - 盘点单完整流程（创建、加明细、完成）
  - 出入库日志
  - 个人中心 + 切换仓库

## 目录

- `app.js` / `app.json` / `app.wxss`
- `utils/`：请求封装、鉴权、格式化
- `pages/login`：登录
- `pages/home`：首页
- `pages/stock`：库存
- `pages/scan`：扫码作业
- `pages/logs`：日志
- `pages/profile`：个人中心
- `pages/orders`：单据中心入口
- `pages/inbound`：入库单流程
- `pages/outbound`：出库单流程
- `pages/check-order`：盘点单流程

## 使用方式

1. 打开微信开发者工具，导入 `wechat-miniprogram` 目录。
2. 在“小程序后台 -> 开发管理 -> 开发设置 -> 服务器域名”中添加：
   - `https://HOST`
3. 勾选小程序权限中的“摄像头”（用于扫码）。
4. 编译后进入登录页，输入 WMS 用户名密码即可。

## 注意事项

- 后端 token 默认 30 分钟过期，过期后会自动跳转登录页。
- 已在 `app.json` 启用按需注入：`"lazyCodeLoading": "requiredComponents"`。
- 小程序请求必须是合法 HTTPS 域名，且证书需被微信客户端信任。
- 当前库存页面为本地搜索（后端 `/stock/` 当前实现无筛选参数）。

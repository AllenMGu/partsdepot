# 多仓库管理系统

## 项目简介

多仓库管理系统是一个专业的库存管理解决方案，支持多仓库、多用户、货物、库位、库存、出入库、盘点等全流程管理。

### 主要功能

- **仪表盘**：展示库存概况、出入库统计、库存预警
- **仓库管理**：多仓库创建与配置
- **库位管理**：库位创建、编辑、禁用
- **货物管理**：货物信息维护
- **库存查询**：多维度库存查询
- **扫码出入库**：快速扫码出入库操作
- **入库单管理**：入库单创建、提交、查询
- **出库单管理**：入库/出库单据全生命周期管理
- **盘点管理**：库存盘点记录
- **用户管理**：用户创建、权限分配
- **LDAP 登录**：支持 LDAP 绑定认证与批量导入
- **微信小程序**：独立的移动端接口客户端（`wechat-miniprogram/`）
- **手机端网页版（H5）**：手机浏览器直接访问的功能完整移动端（`frontend/mobile/`，见下文）

## 技术栈

- 后端：FastAPI + SQLAlchemy 1.x + PostgreSQL（兼容 SQLite）
- 前端：HTML5/CSS3/原生 JavaScript + Tailwind CSS v3 + Font Awesome + Chart.js
- 认证：JWT（HS256）+ bcrypt 密码哈希 + 可选 LDAP 绑定认证

## 项目结构

```
partsdepot/
├── main.py             # 后端入口（FastAPI 单文件应用）
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量示例
├── frontend/           # 前端静态页面（由后端直接托管）
│   ├── index.html      # 登录页面
│   ├── dashboard.html  # 仪表盘（SPA 外壳）
│   ├── dashboard-view.html # 仪表盘视图（支持 iframe 嵌入模式）
│   ├── warehouse.html  # 仓库管理
│   ├── location.html   # 库位管理
│   ├── goods.html      # 货物管理
│   ├── stock.html      # 库存查询
│   ├── scan.html       # 扫码出入库
│   ├── inbound.html    # 入库单管理
│   ├── outbound.html   # 出库单管理
│   ├── check.html      # 盘点管理
│   ├── user.html       # 用户管理
│   ├── common.js       # 通用脚本（含 escapeHtml/XSS 防护）
│   ├── mobile/         # 手机端网页版（H5，见下节）
│   └── assets/         # 资源文件（JS/CSS/图片/字体）
├── wechat-miniprogram/ # 微信小程序客户端
├── gh_mirror_switch.sh # GitHub 直连/镜像站切换工具
└── README.md           # 项目说明
```

## 如何运行

### 1. 准备环境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

参考 `.env.example`（不要把真实凭据提交到版本库）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | 建议必填 | JWT 签名密钥，必须为长随机串。未设置时系统使用一次性随机密钥，**重启后所有已发 token 失效**。 |
| `DATABASE_URL` | 是 | PostgreSQL 生产示例见 `.env.example`；本地试用可 `sqlite:///./warehouse.db` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 首次部署建议 | 系统没有任何用户时，启动自动创建该管理员并授权所有现有仓库；创建成功后即可移除 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | JWT 有效期（分钟），默认 30 |
| `CORS_ORIGINS` | 否 | CORS 允许来源，逗号分隔 |
| `LDAP_*` | 否 | LDAP 服务器 / Base DN / 管理员绑定 / 用户查找过滤器 |

> 安全说明：系统**不再内置任何默认账号密码**，也没有明文密钥兜底。
> 首次登录前必须通过 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 或手动写库创建管理员，
> 否则处于“登录锁定”状态（启动日志会告警）。

### 3. 启动服务

```bash
# 开发
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 生产（多 worker；不要开 --reload）
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
# 或：gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 main:app
```

服务启动后：

- 前端页面：`http://<host>:8000/`（后端自动托管 `frontend/` 目录）
- API 文档：`http://<host>:8000/docs`
- 首次登录：使用 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 创建的管理员账号

### 4. 既有数据库升级注意（重要·部署前置条件）

> **部署前置条件清单（上线前逐项核验）**
> 1. **备份已就位**：代码备份 + 数据库 dump（`pg_dump warehouse_db > wms-db-backup-*.sql`），可回滚；
> 2. **重复库存行预检 = 0 行**（执行下方法 2 的第一条 SQL）；
> 3. **`stock` (仓库,货物,库位) 复合唯一约束已补加**（下方法 2），并在部署后执行"方法 3"的核验 SQL 确认约束存在；
> 4. **部署后冒烟**：登录、仓库/库存查询、一笔出入库、一笔盘点各走一遍，确认无 5xx；
> 5. 应用层防并发依赖**行级锁 + 咨询锁**（代码已含）；数据库级兜底依赖上表约束——两者齐备方可上线。

`Base.metadata.create_all` **不会**修改已存在的表。若你从旧版本升级，请手动执行：

```sql
-- 库存非负约束（新版模型已含；旧库需手工补）
ALTER TABLE stock ADD CONSTRAINT stock_quantity_non_negative CHECK (quantity >= 0);

-- 库存 (仓库,货物,库位) 复合唯一（新版模型已含；旧库缺该约束，
-- 并发首次入库可能产生重复库存行，务必补上；执行前先确认无重复行）
SELECT warehouse_id, goods_id, location_id, count(*) FROM stock
GROUP BY 1,2,3 HAVING count(*) > 1;          -- 应为 0 行
ALTER TABLE stock ADD CONSTRAINT _warehouse_goods_location_uc
    UNIQUE (warehouse_id, goods_id, location_id);

-- 单据号唯一（新版模型已含；部分旧库以 <表名>_order_no_key 命名已存在，
-- 存在同名约束时跳过对应语句）
ALTER TABLE inbound_order_header ADD CONSTRAINT inbound_order_no_key UNIQUE (order_no);
ALTER TABLE outbound_order_header ADD CONSTRAINT outbound_order_no_key UNIQUE (order_no);
ALTER TABLE check_order_header ADD CONSTRAINT check_order_no_key UNIQUE (order_no);
```

**方法 3：部署后核验（部署前置条件清单第 3 项的验证）**

```sql
-- 复合唯一约束必须存在（应返回 1 行 _warehouse_goods_location_uc）
SELECT conname FROM pg_constraint
WHERE conrelid = 'stock'::regclass AND conname = '_warehouse_goods_location_uc';

-- 应用层防并发验证（可选）：并发首入库同组合后应恰好 1 条库存行
SELECT warehouse_id, goods_id, location_id, count(*) FROM stock
GROUP BY 1,2,3 HAVING count(*) > 1;   -- 应恒为 0 行
```

> 2026-09-14 对既有生产库（warehouse_db）只读实测：`stock_quantity_non_negative` CHECK 与三张单据表 `order_no` UNIQUE 均已存在（单据表约束为 `inbound_order_header_order_no_key` 等旧命名）；**`stock` 复合唯一缺失**，且当前无重复库存行，可安全补加。**该补加步骤为部署前置条件**（见本节顶部清单），部署后须以"方法 3"核验。

## 安全基线（本次修复后）

- **JWT**：`SECRET_KEY` 缺失时启动直接失败（无明文兜底）；有效期默认 30 分钟，`ACCESS_TOKEN_EXPIRE_MINUTES` 可覆盖；
- **并发**：出入库/盘点库存扣减使用 `SELECT ... FOR UPDATE` 行锁；单据提交前对单据头加行锁串行化（同一草稿并发重复提交只有第一个生效）；Postgres 咨询锁串行化单号生成，单号取“当天最大尾号+1”（删除草稿不会导致撞号）；
- **出库校验**：同一 (货物, 库位) 的多条明细**先汇总再与库存比较**，拆单不能绕过库存检查；校验与扣减在同一事务内完成；
- **单据明细编辑**：编辑入库/出库明细时，新库位必须属于单据所属仓库（否则 400，不会出现“单据仓库 + 他仓库位”写入库存/流水）；出库明细单价可省略（回退原明细价/物料价），不再 500；
- **首次入库并发建行**：入库（含扫码）同一 (仓库,货物,库位) 的多条明细先按组合汇总、只创建/更新一条库存行；库存行不存在时先持 Postgres 咨询锁串行化建行再重查，避免并发首次入库触发复合唯一约束 500；
- **盘点**：录入时记录系统库存基线；完成时若期间库存已变化（基线≠当前）返回 409 要求重盘，不覆盖盘点期间的合法出入库；
- **扫码出入库**：库存变动、单据头/明细、流水在**单个事务**内一次提交，任一步失败整体回滚；
- **输入**：数量 `gt=0/ge=0` 校验；`stock.quantity >= 0` 数据库级约束兜底；
- **XSS**：前端所有用户可控文本经 `escapeHtml()` 转义后再入 `innerHTML`；
- **Token 存储**：浏览器端统一存 `sessionStorage`（关闭标签页即失效），登录时主动清除 `localStorage` 遗留 token；
- **权限**：仓库/库位/货物的创建修改为管理员专属（库位修改不允许变更所属仓库）；操作员仅出入库+盘点；禁用账号 401；
- **LDAP**：过滤器值按 RFC 4515 转义；配置优先级为“环境变量 > 数据库配置表 > 未配置（LDAP 登录不可用）”，代码不含任何内部环境默认值、缺配置不静默放宽搜索过滤器；配置接口对 `ldap_admin_password` 脱敏返回；LDAP 新用户（首次登录/批量导入）默认**零仓库权限**（由管理员分配）；**每次加载都从空值重新构造完整配置**——数据库配置被删除/清空且无环境变量时，上一次加载的旧服务器/凭据/过滤器立即失效（登录 401），不会用旧值继续认证；
- **初始管理员**：仅可通过 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 环境变量自举（且仅当数据库无任何用户时），无内置弱口令。

## 回归测试

评审修复项的回归测试已随仓库提供（本地 SQLite 即可运行，无需 PostgreSQL）：

```bash
pip install -r requirements.txt
bash tests/run_regression.sh
```

覆盖：出库拆单/并发重复提交、盘点基线冲突、扫码单事务、LDAP 未配置降级与配置撤销、零仓库授权、库位越权、入库/出库明细编辑（跨仓校验、单价可选）、入库单同(货物,库位)多条明细、单号撞号、JWT 有效期、管理员自举等，共 61 项 HTTP 检查 + 3 项进程内 LDAP 加载检查。详见 `tests/README.md`。

## 手机端网页版（H5）

功能对标微信小程序（`wechat-miniprogram/`），手机浏览器直接访问，无需安装：

- **入口**：`http://<host>:8000/mobile/index.html`（后端同域托管，登录态与桌面端共享 Cookie 会话）
- **页面**（均在 `frontend/mobile/` 下）：
  | 页面 | 功能 |
  | --- | --- |
  | `index.html` | 首页：库存概览 + 功能入口（匿名可见申请入口） |
  | `login.html` | 登录（`/token`，HttpOnly Cookie 会话） |
  | `apply.html` | 备件申请：**免登录**提交，仓库选择、货物搜索（350ms 防抖）、批量库存刷新（`POST /public/stock-lookup`）、提交后展示申请编号 |
  | `stock.html` | 库存查询（当前仓库过滤 + 关键字筛选） |
  | `scan.html` | 扫码出入库：BarcodeDetector 相机扫码（不支持时回退手动输入/扫码枪）、库位联想、当前库存参考、出库不超库存校验 |
  | `orders.html` | 单据中心（入库单/出库单/盘点单入口） |
  | `inbound.html` / `outbound.html` | 单据列表、创建、明细增删、提交；入库单完成后可「退库」 |
  | `check.html` | 盘点单：创建、逐项扫码确认数量、差异展示、完成盘点 |
  | `logs.html` | 出入库日志 |
  | `profile.html` | 我的：用户信息、切换仓库、退出登录 |
- **实现约定**（与仓库 CSP 策略一致）：全部脚本外置、零内联 `<script>`、零内联事件处理器（统一 `data-act`/`data-scan` 事件委托）；每页 meta CSP `script-src 'self'`；API 基址相对路径 `../api`（任意子路径部署可用）。
- **扫码说明**：相机扫码依赖 `BarcodeDetector` + `getUserMedia`（需 HTTPS 或 localhost，Android Chrome 等支持）；不支持时可外接扫码枪（键盘模式）或手动输入。

## GitHub 镜像切换（网络不稳时）

本机/部分网络访问 `github.com` 可能不稳定（SYN 丢失、fetch 超时）。仓库自带切换工具：

```bash
./gh_mirror_switch.sh status    # 查看当前 fetch/push URL + 探测直连/镜像可达性
./gh_mirror_switch.sh mirror    # fetch 切到镜像（gh-proxy.com），push 仍直连
./gh_mirror_switch.sh direct    # fetch/push 均直连 github.com
./gh_mirror_switch.sh auto      # 自动：直连优先；直连挂、镜像活才切镜像
```

安全约定：
- **镜像只用于 fetch**（ls-remote/fetch/pull）；**push 永远直连 github.com**（`git remote set-url --push`），凭证与推送内容不经过任何第三方代理；
- `auto` 只在「直连不可达 且 镜像可达」时才切镜像；两者都不可达时保持现状；
- `origin` 是自定义 URL（fork/内网等）时，`auto` 一律不改动；
- 只修改 `origin` 的 fetch/push URL，不影响其它远端与配置。

## 注意事项

1. 金额/数量当前为 `Float`（十进制精度迁移不在本次范围，财务对账敏感场景建议后续改 `Numeric`）。
2. 生产部署请置于反向代理（nginx 等）之后，启用 HTTPS；CORS 按实际来源收紧。
3. 部分功能需要管理员权限（用户/仓库/库位/货物维护）；操作员仅可使用出入库、盘点与查询。
4. 建议使用现代浏览器（Chrome、Firefox、Edge 等）访问系统。

<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/0e1ef5ed-5436-44ce-81d3-2135918ededb" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/7d197729-09d5-4875-bbd1-faaa65e2583c" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/2635a119-277f-4673-9936-17d75ef557bc" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/89ae0998-8b9f-4f81-a11a-006a9ea07dab" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/44d89f5a-c62c-4a78-801a-e5f2b39e9341" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/6eedd286-55c4-4cd7-a0ae-cfd08155a723" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/ef27b20f-3e72-4776-96e3-881e7be9344f" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/800a0fdc-1ef3-4ab8-874a-fa5766c7f6e4" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/b5d2d2ba-3df5-4464-a975-b4919c69ddbe" />
<img width="2550" height="1255" alt="image" src="https://github.com/user-attachments/assets/ca534d23-cf1e-4955-aa67-aa4d3eb70290" />

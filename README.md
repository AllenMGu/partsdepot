# 办公用品申请系统

面向公司内部办公用品申请、库存与仓库管理的一体化系统，支持桌面 Web、手机 H5 和微信小程序。

系统以“申请 → 审批/处理 → 库存 → 出入库 → 盘点 → 查询追溯”为主线，同时保留多仓库、多库位、多用户和 LDAP 等后台管理能力。

> 仓库历史上由 WMS / 备件管理项目演进而来，因此部分数据库名、环境变量名、内部模块名和测试脚本仍保留 `WMS` / `warehouse` 命名；用户界面统一使用“办公用品申请系统”。

## 主要功能

### 办公用品申请

- **免登录申请**：员工无需系统账号即可提交办公用品申请。
- **仓库选择**：申请时明确所属仓库，库存展示与后续处理仓库保持一致。
- **货物搜索与库存提示**：支持按名称/条码搜索，并展示申请仓库当前可用库存。
- **多行申请**：一张申请单可添加多个货物并分别填写数量。
- **申请单编辑**：管理端可对申请货物进行增加、删除和修改。
- **申请处理**：管理员可查看、通过或驳回申请；处理过程保留状态与操作记录。
- **自动归档**：已处理申请按配置周期自动归档，也支持管理员手动归档。

### 库存与仓库

- **仪表盘**：库存概况、出入库统计和库存预警。
- **仓库管理**：多仓库创建与配置。
- **库位管理**：库位创建、编辑、禁用及仓库归属校验。
- **货物管理**：货物信息维护、Excel 导入/导出。
- **库存查询**：按仓库、货物、库位和关键字查询，可导出库存数据。
- **扫码出入库**：支持摄像头扫码、扫码枪/手动输入；移动端支持连续扫码工作流。
- **入库单管理**：创建、编辑、提交、查询和导出。
- **出库单管理**：创建、编辑、提交、库存校验、查询和导出。
- **盘点管理**：创建盘点单、扫码盘点、差异确认、完成及报告导出。
- **库存日志**：记录库存变化与出入库流水。

### 用户与终端

- **用户管理**：用户创建、启停、角色和仓库权限分配。
- **LDAP 登录**：支持 LDAP 绑定认证、配置管理和用户批量导入。
- **桌面 Web**：SPA 外壳 + iframe 嵌入视图，支持桌面管理操作。
- **手机端 H5**：手机浏览器直接访问，无需安装 App。
- **微信小程序**：提供移动端登录、申请、库存和业务操作入口。

## 技术栈

- **后端**：FastAPI + SQLAlchemy + PostgreSQL（兼容 SQLite 测试）
- **前端**：HTML5 / CSS3 / 原生 JavaScript + Tailwind CSS v3 + Font Awesome + Chart.js
- **认证**：JWT（HS256）+ bcrypt + HttpOnly Cookie / Bearer Token + 可选 LDAP
- **测试**：HTTP 回归 + PostgreSQL 并发专项 + Playwright E2E + 小程序单元测试
- **CI**：GitHub Actions

## 项目结构

```text
partsdepot/
├── main.py                 # FastAPI 应用入口、路由装配、静态托管、后台归档任务
├── core/                   # 核心层：配置、数据库、模型、Schema、认证、LDAP、通用依赖
├── api/                    # 业务 API
│   ├── users.py
│   ├── warehouses.py
│   ├── locations.py
│   ├── goods.py
│   ├── stock.py
│   ├── inbound.py
│   ├── outbound.py
│   ├── check.py
│   └── requests.py         # 免登录申请、库存查询、管理处理、归档
├── frontend/               # 桌面端与 H5 静态前端
│   ├── index.html          # 桌面登录
│   ├── dashboard.html      # SPA 外壳
│   ├── dashboard-view.html # 仪表盘嵌入视图
│   ├── request.html        # 免登录申请页
│   ├── request-admin.html  # 申请管理
│   ├── warehouse.html
│   ├── location.html
│   ├── goods.html
│   ├── stock.html
│   ├── scan.html
│   ├── inbound.html
│   ├── outbound.html
│   ├── check.html
│   ├── user.html
│   ├── mobile/             # 手机端 H5
│   └── assets/             # JS / CSS / 图片 / 字体
├── wechat-miniprogram/     # 微信小程序
├── migrations/             # 数据库迁移/升级辅助脚本
├── tests/                  # 回归、并发、E2E 与测试说明
├── .github/workflows/      # CI
├── .env.example            # 环境变量示例
├── requirements.txt
├── gh_mirror_switch.sh     # GitHub 直连/镜像切换工具
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 配置环境变量

参考 `.env.example`。生产环境不要把真实凭据提交到版本库。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | **是** | JWT 签名密钥；代码未配置默认值，缺失时服务拒绝启动 |
| `DATABASE_URL` | **是** | PostgreSQL 生产连接串；本地测试可使用 SQLite |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 首次部署建议 | 数据库无用户时自动创建初始管理员并授权现有仓库 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | JWT 有效期，默认 30 分钟 |
| `CORS_ORIGINS` | 否 | 允许的 CORS 来源，逗号分隔 |
| `AUTH_COOKIE_SECURE` | 否 | HTTPS 生产环境建议设为 `true` |
| `LDAP_*` | 否 | LDAP Server / Base DN / 管理员绑定 / 用户过滤器 |

生成 `SECRET_KEY` 示例：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

本地 SQLite 示例：

```bash
export SECRET_KEY='replace-with-a-long-random-value'
export DATABASE_URL='sqlite:///./warehouse.db'
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='replace-with-a-strong-password'
```

### 3. 启动服务

开发：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

生产：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

服务启动后：

- 桌面端：`http://<host>:8000/`
- 手机 H5：`http://<host>:8000/mobile/index.html`
- 免登录申请：`http://<host>:8000/request.html`
- API 文档：`http://<host>:8000/docs`

## 数据库升级注意

`Base.metadata.create_all()` 只负责创建不存在的表，**不会自动给旧表补约束或完成结构迁移**。从旧版本升级时，务必先备份数据库并按实际版本检查迁移要求。

生产部署至少应确认：

1. 已完成代码与数据库备份，可回滚。
2. `stock` 不存在重复的 `(warehouse_id, goods_id, location_id)` 行。
3. `stock` 已存在 `(warehouse_id, goods_id, location_id)` 复合唯一约束。
4. `stock.quantity` 已存在非负 CHECK 约束。
5. 入库、出库、盘点单号具有唯一约束。
6. 部署后完成登录、申请、库存查询、出入库、盘点和导出冒烟测试。

重复库存预检：

```sql
SELECT warehouse_id, goods_id, location_id, count(*)
FROM stock
GROUP BY 1,2,3
HAVING count(*) > 1;
```

复合唯一约束示例：

```sql
ALTER TABLE stock
ADD CONSTRAINT _warehouse_goods_location_uc
UNIQUE (warehouse_id, goods_id, location_id);
```

非负库存约束示例：

```sql
ALTER TABLE stock
ADD CONSTRAINT stock_quantity_non_negative
CHECK (quantity >= 0);
```

> 对既有生产库执行任何 DDL 前，请先核对约束是否已存在，避免重复创建。

## 安全与一致性基线

- **启动配置**：`SECRET_KEY` 和 `DATABASE_URL` 缺失时直接拒绝启动。
- **认证**：JWT + Cookie/Bearer 双通道；禁用用户无法继续访问受保护接口。
- **初始管理员**：仅通过环境变量自举，不内置默认弱口令。
- **权限**：仓库、库位、货物等维护操作按管理员权限控制；业务用户只获得授权仓库范围内能力。
- **LDAP**：过滤器值转义；配置优先级为环境变量 > 数据库 > 未配置；敏感密码不明文返回。
- **库存并发**：关键库存变更使用行级锁和 PostgreSQL 咨询锁，降低重复建行、重复提交和并发过账风险。
- **库存约束**：数据库非负约束与复合唯一约束作为应用层防护之外的最终兜底。
- **事务一致性**：扫码出入库、单据过账等关键库存操作在单事务中执行，失败整体回滚。
- **盘点保护**：记录盘点基线，盘点期间库存已变化时拒绝直接覆盖并要求重新确认。
- **申请接口**：公共申请/搜索接口带 IP 与全局限流，输入字段做长度和格式校验。
- **XSS**：用户可控文本输出前统一转义。
- **CSP**：前端页面使用 Content-Security-Policy；内联脚本哈希变更需同步更新 CSP。

## 回归测试与 CI

### 本地基础回归

```bash
pip install -r requirements.txt
bash tests/run_regression.sh
```

### PostgreSQL 并发专项

```bash
WMS_DATABASE_URL='postgresql://user:password@localhost/wms_pgconc' \
python tests/pg_concurrency.py
```

### Playwright E2E

```bash
pip install playwright
playwright install chromium
bash tests/run_pw_e2e.sh
```

GitHub Actions 当前包含：

- PostgreSQL 回归测试
- PostgreSQL 并发专项
- Playwright 浏览器端到端测试
- 小程序申请页单元测试

测试脚本具有测试库安全护栏，**不要把生产数据库连接串交给测试脚本**。详细测试场景和当前检查项数量见 `tests/README.md`。

## 手机端网页版（H5）

手机浏览器可直接访问 `frontend/mobile/`，无需安装 App。

| 页面 | 功能 |
| --- | --- |
| `mobile/index.html` | 首页、库存概览、功能入口 |
| `mobile/login.html` | 登录 |
| `mobile/apply.html` | 免登录办公用品申请、仓库选择、货物搜索和库存提示 |
| `mobile/stock.html` | 库存查询 |
| `mobile/scan.html` | 摄像头扫码/扫码枪/手动输入、扫码出入库 |
| `mobile/orders.html` | 单据中心 |
| `mobile/inbound.html` | 入库单 |
| `mobile/outbound.html` | 出库单 |
| `mobile/check.html` | 盘点 |
| `mobile/logs.html` | 出入库日志 |
| `mobile/profile.html` | 用户信息、切换仓库、退出登录 |

相机扫码依赖 `getUserMedia`，生产环境请使用 HTTPS。浏览器不支持相机扫码能力时，可使用扫码枪或手动输入作为回退方式。

## 微信小程序

小程序代码位于 `wechat-miniprogram/`。目前 UI 标题统一为“办公用品申请系统”，并提供移动端登录、申请、库存及相关业务入口。

部署小程序时需按实际环境配置合法域名、HTTPS 和后端 API 地址；不要把生产凭据写入仓库。

## 导入与导出

当前桌面端支持多类业务数据导入/导出：

- 货物 Excel 导入、导出
- 库存导出
- 入库单导出
- 出库单导出
- 盘点报告导出

SPA 嵌入模式对带 `download` 属性及动态下载链接保留浏览器原生下载行为，避免导出动作被站内路由拦截。

## GitHub 镜像切换

仓库提供 `gh_mirror_switch.sh`，用于网络不稳定时在 GitHub 直连和镜像 fetch 之间切换：

```bash
./gh_mirror_switch.sh status
./gh_mirror_switch.sh mirror
./gh_mirror_switch.sh direct
./gh_mirror_switch.sh auto
```

安全约定：

- 镜像仅用于 `fetch` / `pull`。
- `push` 始终直连 `github.com`。
- `auto` 仅在直连不可达且镜像可达时切换。
- 自定义 `origin` 不自动改写。

## 部署建议

1. 生产环境使用 PostgreSQL，并放在 nginx 等反向代理之后。
2. 强制 HTTPS，并设置 `AUTH_COOKIE_SECURE=true`。
3. 收紧 `CORS_ORIGINS`，只允许实际业务域名。
4. 生产数据库与测试数据库完全隔离。
5. 部署前备份数据库，部署后执行关键业务冒烟测试。
6. 对数据库结构变更使用显式迁移流程，不依赖 `create_all()` 自动升级旧表。
7. 合并代码前保持 GitHub Actions 全绿。

## 说明

- 金额/数量当前仍使用浮点类型的部分场景，财务精度敏感需求后续应迁移到 `Numeric/Decimal`。
- 浏览器建议使用当前版本 Chrome / Edge / Firefox。
- 摄像头扫码需 HTTPS 或 localhost 安全上下文。
- 详细测试保护、并发场景和测试数据库要求见 `tests/README.md`。

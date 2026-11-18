# cc-manifest-lab

CC_Codex 二期（26.11）**数据工程**产品线：**湖区落地清单台**（Litestar + Preact CDN + SQLite）。

## 一句话骨架（自有，非外仓抄题）

**湖区落地批次 → 对象指纹登记 → 清单闭合 → 可见性对账**

与现有七线正交（不是采集目录、ETL 管道、同步源、解析入库、编排流、种子摄取、留存裁撤）。
不碰外仓常见指纹（Feed 归并 / CDC 游标 / 质检签发 / Slicegate 类仓名）。

## 领域

- **所属领域**：数据工程
- **子方向**：数仓与数据湖（落地清单 / 对象登记闭环）

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12、Litestar、Uvicorn、SQLite |
| 前端 | 原生 JS SPA（全中文，侧栏），靛青竖纹 + 亮青强调；不依赖外网 CDN |
| 测试 | pytest + httpx（本地秒级闭环，优先于 compose） |

## 基线已通主链

1. 湖区落地批次列表（收集中 / 已闭合）
2. 向批次登记对象键与内容指纹
3. 闭合清单（对象数固化，拒绝空批）
4. 可见性对账报告（闭合批 vs 开放批 vs 孤儿对象）

## 页库对齐裁决（manifest-bug-14）

页面携带页态（`expected_status`）点登记/闭合，引擎在排他写事务（`BEGIN IMMEDIATE`）内核对库态：

- **页态 = 库态**：正常放行。
- **页态 ≠ 库态**：裁决只钉死一条 —— 库态为终态则**回滚**（整笔回滚页面乐观态，写 `rollback_ledger` 回滚流水）；库态仍可流转则**强制刷**（原子复核钉死库态，落 `force_refresh_vouchers` 凭证，临界操作按刷新后库态重估）。回滚流水与强制刷凭证**互斥落地**，每次裁决另存一份页库复现单（`drift_repros`）。
- 批次水线亮库态芯片与裁决编号；清洗回放台（`/api/adjudications`）对照同一编号；批次详情附本机库存证（`/api/batches/{id}` 的 `proof`）。
- 出错回白话并带裁决编号；页面失败后自动按库态重对齐，漂了就拒。

预留切口（未做，供后续功能题）：清单换版生效窗、闭合作废回开放、批量失败闭互斥、跨区依赖冻结、清单回灌等。

## 本地（推荐反馈环）

```powershell
cd claude-workspace\cc-manifest-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python run.py
```

http://127.0.0.1:6900/login · **admin / 123456**

## Docker

```powershell
docker compose up -d --build
```

端口 **6900**。冒烟：`python ..\scripts\_smoke_lab_access.py --dir .`

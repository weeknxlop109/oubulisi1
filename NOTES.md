# 运行说明（NOTES）

## 一键运行（三种方式任选其一）

| 方式 | 命令 | 适用 |
|---|---|---|
| Docker（推荐，验收统一环境） | `docker compose -p medqa_001 up -d --wait` | 任意平台，卷持久化数据/模型/日志 |
| PowerShell（Windows） | `.\up.ps1` | 本机 venv 已装依赖 |
| Shell（macOS / Linux / 容器） | `./up.sh` | 无 .venv 时自动创建并安装依赖 |

首次运行自动完成：**inbox 入库 → 建图 → 题库生成 → 模型训练**（全部幂等，可重复执行），随后启动服务 http://localhost:8000 。
全量验证：`./verify.sh` 或 `.\verify.ps1`（20 项判据，输出 logs/verify-<时间戳>.log；V6b 备份真实推送演练默认用本地 bare 仓库，配置 BACKUP_REPO 后推真实仓库）。

## 接口速查

- `GET /health` 组件状态灯（graph/model/dict/题库/备份）
- `POST /ask` 问答：`{"question": "...", "session_id": "可选"}`
- `GET /demo/cases` 必演用例
- `POST /admin/kb/sync` 手动触发 inbox 入库
- `GET /admin/kb/stats` 知识库/题库统计
- `POST /admin/backup/run`、`GET /admin/backup/status` 备份
- 网页演示：`http://localhost:8000/`（PWA，可添加到主屏）
- 移动端 APP 壳：`mobile/index.html`（手机与电脑同 Wi-Fi，替换 `http://<本机IP>:8000`）

## 已知问题

1. 5 类问句（治疗方案/治疗周期/并发症/相互作用/症状推疾病）为**待扩展**状态：题库中有显式标注与扩展路径，问答命中时返回 `status=pending` 并给出扩展说明，不静默缺失。
2. 验收环境无 pymongo / neo4j 驱动且不允许新增依赖，图存储用本地 JSON（`data/graph.json`，按 Label+name MERGE 去重），查询用**参数化 Cypher 子集执行器**（`cypher_engine.py`），模板与真实 Neo4j Cypher 同构，后续可无缝替换驱动。
3. 备份默认 `BACKUP_DRY_RUN=true`：只打包 + 敏感扫描 + 报告，不推送；配置 `BACKUP_REPO` 并置 `false` 后 git 命令行推送私有仓库。
4. 训练数据为自动题库 + 问句增广，BIO 标注由实体词典匹配生成（词典版 baseline），指标 F1/acc 绑定 8:1:1 切分的测试集（`models/medical_ner_cls/metrics.json`）。

## 仓库说明

- GitHub 私有仓库地址见 `submission.json.repo_url`（只读邀请说明可另行提供）。
- 定时备份：服务启动后内建调度线程按 `BACKUP_CRON_HOUR`（默认每日 02:00）执行；`.env` 与模型权重、数据库卷、原文日志均不入包（`backup/backup.py` 白名单 + 排除清单）。

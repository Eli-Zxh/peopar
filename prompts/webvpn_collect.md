# webvpn_collect.md — 机构 webvpn 采集操作手册（v1，2026-08-27）

> 定位：B 层订阅/中文源（**Scopus / CNKI 知网 / 万方**）接入。半自动：
> **用户手动完成 webvpn 登录**（机构门户，常有验证码/2FA，不适合脚本自动登录），
> **AI 助手负责检索、导出与入库**。本项目无常驻进程、无自动调度；本手册为 skill 会话指引。

## 流程总览

```
用户：浏览器打开机构 webvpn 门户 → 登录 → 保持会话（不关闭）
  ↓
AI 助手：在门户内检索（或指导用户检索）→ 导出标准题录文件
  ↓
导入：python3 manage/webvpn.py import <文件> --source scopus|cnki|wanfang --domain <域>
  ↓
图谱刷新：python3 analyze/graph.py <域>  →  失效感知：python3 manage/snapshot.py staleness <域>
```

## 一、用户侧（必须人工完成）

1. 打开机构 webvpn 门户（如 `https://vpn.xxx.edu.cn`），用校园账号登录。
2. 通过门户的 URL 重写代理进入目标数据库：
   - **Scopus**：`https://www.scopus.com`（webvpn 重写链接）
   - **CNKI 知网**：`https://www.cnki.net` 或 `https://kns.cnki.net`
   - **万方**：`https://www.wanfangdata.com.cn`
3. 保持浏览器登录态（验证码/2FA 由用户处理）；**登录完成前不要执行导入**。

## 二、检索与导出（AI 助手执行）

### Scopus
- 用检索式（如 `TITLE-ABS-KEY(aphasia AND tDCS)`），可限定年份。
- 导出：搜索结果 → 全选/勾选 → **Export → CSV**（勾选字段：Author full names、
  Title、Year、DOI、Source title、Affiliations、Cited by、Abstract）。
- 保存为 `scopus_<检索标签>_<日期>.csv`。

### CNKI 知网
- 检索后：勾选文献 → **导出/参考文献 → 导出格式选「NoteExpress」或「EndNote」**（RIS 文本）。
- 保存为 `cnki_<检索标签>_<日期>.txt`（或 .ris）。

### 万方
- 检索后：勾选 → **导出 → 选择「NoteExpress / RIS」**。
- 保存为 `wanfang_<检索标签>_<日期>.txt`。

> 中文源导出通常带汉字作者名与中文机构；导入器按汉字名 + 机构短键归并，未命中新建待校对。

## 三、导入（AI 助手执行）

```bash
python3 manage/webvpn.py import data/webvpn/scopus_neuro_tdcs_20260827.csv --source scopus --domain neuroling --query "TITLE-ABS-KEY(aphasia AND tDCS)"
python3 manage/webvpn.py import data/webvpn/cnki_失语症_20260827.txt --source cnki --domain neuroling --query "失语症 AND tDCS"
python3 manage/webvpn.py list          # 查看批次
```

- 同一文件重复导入会被文件指纹拦截（`duplicate`）。
- 收录规则留痕为 `keyword` + `matched_term=webvpn:<source>:<检索式>`（本质是用户在源站
  执行的关键词检索），可在 `paper_domains` 与审计中溯源。
- 导入后可调用 `POST /api/webvpn/import`（插件管理台也可传文件内容）。

## 四、后续

1. `python3 analyze/graph.py <域>` 刷新共著图谱与方向簇。
2. `python3 manage/snapshot.py staleness <域>` 标记论文集合已变化的快照（不自动重合成）。
3. 中文论文作者多为新建外围作者（别名待校对）——在管理台「待校对队列」处理。
4. 核心作者的机构官网信息抓取校验见 skill 工作流 5（`manage/affiliations.py`）。

## 红线

- 只导入**用户已授权访问**的源（机构订阅范围内）；遵守各库导出条款。
- 收录判定无 LLM：导入是搬运用户在源站的检索结果，不引入模型判断。
- 全部写入留痕（webvpn_imports + audit_log），可审计、可回查。

# researcher_deepdive.md — 研究者全面画像与标签深描模板（v1，2026-09）

> **平级独立 skill**：由用户手动触发（点名某位重要研究者），不做批量触发。
> 前置：该研究者已有确定性记录（论文/方向归属/基础机构）与（可选）简单画像。

## 角色与任务

你是学术情报整合助手。目标研究者已有本地证据（论文、方向、简单画像）。请进一步基于**可获得的机构公开信息**
（任职机构官网、实验室/课题组页、ORCID 公开页）与该研究者既往论文，为其产出**全面画像与标签建议**。
深描不是简单复述论文列表，而是把散落信息整合成可读、可下钻的画像，并补全机器可检索的标签。

## 输入

- 本地：`data/pack_authors_<domain>_<n>.json` 中该研究者的作者对象（若已导出）
  （papers/clusters/collaborators/flags/aliases/affiliations）
- 外部（agent 浏览器/curl 采集；**仅机构公开信息，从简**）：机构官网、课题组页、ORCID

## 采集边界（红线）

1. 只采集机构官网 / 实验室页 / ORCID 等公开页；**不采集**社交账号内容、新闻聚合、付费内容。
2. 每条外部信息记录来源 URL 与访问日期；无法核实的信息一律不写入。
3. 隐私/敏感信息（私人邮箱、个人联系方式）不采；只保留机构公开联系方式（机构邮箱、办公电话可见于官方页时）。

## 产出（严格 JSON）

```json
{
  "author_id": "BG000117",
  "prompt_ver": "researcher_deepdive.md@v1-202609",
  "model": "<agent 标识与日期>",
  "career": {
    "current": {"institution": "...", "role": "教授/研究员/博导…", "dept": "...", "url": "…", "accessed": "2026-09-04"},
    "past": [{"institution": "...", "role": "...", "period": "20xx-20xx", "url": "…"}]
  },
  "profile": "2-4 段全面画像：在方向中的定位、主要学术贡献、代表方法与成就（奖项/要职如可公开核实）。每段可锚定论文或来源 URL。",
  "tag_suggestions": [
    {"tag": "失语症", "basis": "来自机构页研究介绍 + 代表论文锚定"},
    {"tag": "言语数字标志物", "basis": "..."}          ← 词表外新词自动走 propose
  ],
  "note": "供人工批注的一句话定位（可含联系建议，仅内部）",
  "sources": [{"url": "...", "title": "...", "accessed": "..."}]
}
```

## 硬约束

1. 所有外部断言必须有 source_url 或本地论文锚定；无来源不写（宁可少写）。
2. tag 从既有受控词表选（`python3 manage/tags.py list-vocab`），词表外的新词单独列入
   `new_tags`（将经 propose → 人工裁决入表），不直接塞入正式标签。
3. 不评价研究质量/人；不把任职表述与造假标记关联；涉 L0/L1 者仅客观转述既有标记。
4. 输出必须是可 `json.loads` 的单个 JSON 对象。

## 落库方式（由 agent 执行）

按 `manage/tags.py` 子命令逐步落：

```
python3 manage/tags.py propose-vocab <new_tags.json>     # 词表外新词（可选）
python3 manage/tags.py suggest <deepdive.json>           # 研究者标签建议（pending）
python3 manage/affiliations.py add <BG…> --institution "…" --role "…" --url "…" --by <署名>   # 机构履历
```

标签/机构信息默认 pending/待校验，用户在插件管理台或 CLI `review`/`verify` 批准后生效；
深描画像正文经 `notes_sync` 或直接作为研究者 vault 笔记批注保留。

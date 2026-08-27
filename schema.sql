-- 百官行述 (peopar) — SQLite schema
-- 横切原则：研究者 ID 系统自建；外部 ID 仅是归并线索；一切标记可下钻来源；人工操作全程留痕；簇只增不改。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============ 领域 ============
CREATE TABLE IF NOT EXISTS domains (
    id          TEXT PRIMARY KEY,            -- 如 neuroling / ad_lesne
    name        TEXT NOT NULL,
    description TEXT,
    config_path TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============ 研究者（百官） ============
CREATE TABLE IF NOT EXISTS authors (
    id           TEXT PRIMARY KEY,           -- 系统自建 ID：BG000001...
    name_display TEXT NOT NULL,              -- 规范显示名（拼音规范形）
    name_sort    TEXT,                       -- 去音调规范化（检索用）
    name_zh      TEXT,                       -- 汉字真名（校对后）
    pinyin_norm  TEXT,                       -- 归一化拼音（小写去空格）
    openalex_id  TEXT,
    orcid        TEXT,
    tier         TEXT NOT NULL DEFAULT 'peripheral'
                 CHECK (tier IN ('core','peripheral')),   -- 双层制
    tier_reason  TEXT,                       -- 升核依据
    status       TEXT NOT NULL DEFAULT 'active',
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_authors_openalex ON authors(openalex_id);
CREATE INDEX IF NOT EXISTS idx_authors_pinyin ON authors(pinyin_norm);

-- 全局别名表：汉字真名 / 拼音 / 署名变体 / 外部 ID
CREATE TABLE IF NOT EXISTS author_aliases (
    id         INTEGER PRIMARY KEY,
    author_id  TEXT NOT NULL REFERENCES authors(id),
    alias      TEXT NOT NULL,
    alias_type TEXT NOT NULL CHECK (alias_type IN ('hanzi','pinyin','variant','external')),
    source     TEXT NOT NULL,                -- pubmed/openalex/manual/llm_suggest
    confidence REAL,
    verified   INTEGER NOT NULL DEFAULT 0,   -- 0=待校对 1=已确认
    verified_by TEXT,
    verified_at TEXT,
    UNIQUE (author_id, alias, alias_type)
);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON author_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_aliases_unverified ON author_aliases(verified) WHERE verified = 0;

-- 时间履历（机构×年份），三来源共存
CREATE TABLE IF NOT EXISTS affiliations (
    id              INTEGER PRIMARY KEY,
    author_id       TEXT NOT NULL REFERENCES authors(id),
    institution     TEXT NOT NULL,
    institution_norm TEXT,
    start_year      INTEGER,
    end_year        INTEGER,
    source_tag      TEXT NOT NULL CHECK (source_tag IN ('auto','web','manual','llm_pending')),
    source_url      TEXT,
    confidence      REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_aff_author ON affiliations(author_id);

-- 归并记录（可审计）
CREATE TABLE IF NOT EXISTS author_merges (
    id        INTEGER PRIMARY KEY,
    kept_id   TEXT NOT NULL REFERENCES authors(id),
    merged_id TEXT NOT NULL,
    reason    TEXT,
    actor     TEXT NOT NULL DEFAULT 'system',
    ts        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============ 论文 ============
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY,
    doi             TEXT,
    pmid            TEXT UNIQUE,
    openalex_id     TEXT,
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,           -- 规范化标题（三级主键末级）
    year            INTEGER,
    pub_date        TEXT,
    journal         TEXT,
    volume          TEXT,
    pages           TEXT,
    abstract        TEXT,
    pub_types       TEXT,                    -- JSON 数组
    mesh            TEXT,                    -- JSON 数组
    language        TEXT,
    cited_by_count  INTEGER DEFAULT 0,
    retraction_status TEXT NOT NULL DEFAULT 'none'
                    CHECK (retraction_status IN ('none','retracted','corrected','concern','questioned')),
    first_author_norm TEXT,                  -- 归并辅助
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_titlenorm ON papers(title_norm, first_author_norm, year);

-- 收录判定留痕：每篇论文因何规则进入哪个域
CREATE TABLE IF NOT EXISTS paper_domains (
    paper_id     INTEGER NOT NULL REFERENCES papers(id),
    domain_id    TEXT NOT NULL REFERENCES domains(id),
    rule         TEXT NOT NULL CHECK (rule IN ('mesh','keyword','cite_seed','author')),
    matched_term TEXT,
    PRIMARY KEY (paper_id, domain_id)
);

CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id       INTEGER NOT NULL REFERENCES papers(id),
    author_id      TEXT NOT NULL REFERENCES authors(id),
    position       INTEGER,
    is_corresponding INTEGER NOT NULL DEFAULT 0,
    raw_name       TEXT,                     -- 源署名原文
    PRIMARY KEY (paper_id, author_id)
);
CREATE INDEX IF NOT EXISTS idx_pa_author ON paper_authors(author_id);

-- 采集暂存：PubMed 阶段的原始作者槽位（未消歧），OpenAlex 阶段解析后回填 paper_authors
CREATE TABLE IF NOT EXISTS paper_author_staging (
    paper_id    INTEGER NOT NULL REFERENCES papers(id),
    position    INTEGER NOT NULL,
    raw_name    TEXT NOT NULL,
    pinyin_norm TEXT,
    affiliation TEXT,
    resolved_author_id TEXT REFERENCES authors(id),
    PRIMARY KEY (paper_id, position)
);
CREATE INDEX IF NOT EXISTS idx_staging_unresolved ON paper_author_staging(paper_id) WHERE resolved_author_id IS NULL;

-- 引用边（论文→论文），支撑种子扩散与证据链
CREATE TABLE IF NOT EXISTS citations (
    citing_id INTEGER NOT NULL REFERENCES papers(id),
    cited_id  INTEGER NOT NULL REFERENCES papers(id),
    source    TEXT NOT NULL DEFAULT 'pubmed_elink',
    PRIMARY KEY (citing_id, cited_id)
);
CREATE INDEX IF NOT EXISTS idx_cit_cited ON citations(cited_id);

-- ============ 方向簇（只增不改） ============
CREATE TABLE IF NOT EXISTS clusters (
    id         INTEGER PRIMARY KEY,
    domain_id  TEXT NOT NULL REFERENCES domains(id),
    label      INTEGER NOT NULL,             -- 批次内标签号
    name       TEXT,                         -- 人工/LLM 命名
    signature  TEXT,                         -- 术语签名 JSON
    batch_id   TEXT NOT NULL,                -- 计算批次；历史簇永不重算
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (domain_id, batch_id, label)
);

CREATE TABLE IF NOT EXISTS author_clusters (
    author_id  TEXT NOT NULL REFERENCES authors(id),
    cluster_id INTEGER NOT NULL REFERENCES clusters(id),
    weight     REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (author_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_ac_cluster ON author_clusters(cluster_id);

-- 方向快照（LLM 合成 + 人工在环）
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    cluster_id   INTEGER NOT NULL REFERENCES clusters(id),
    content      TEXT NOT NULL,              -- 定义/代表文献/时间线/分歧点（JSON）
    model        TEXT,                       -- 合成者标识（agent 会话/模型名）
    prompt_ver   TEXT,                       -- 提示词模板版本（文件名@日期）
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','affected_pending_review','revised','superseded')),
    review_status TEXT NOT NULL DEFAULT 'pending'
                 CHECK (review_status IN ('pending','approved','rejected')),
    reviewed_by  TEXT,
    reviewed_at  TEXT,
    supersedes   INTEGER REFERENCES snapshots(id),
    generated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 证据链：结论→支撑论文
CREATE TABLE IF NOT EXISTS evidence (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    paper_id    INTEGER NOT NULL REFERENCES papers(id),
    role        TEXT,
    PRIMARY KEY (snapshot_id, paper_id)
);

-- ============ 造假事件与标记 ============
CREATE TABLE IF NOT EXISTS fraud_events (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE,
    title       TEXT NOT NULL,
    description TEXT,
    domain_id   TEXT REFERENCES domains(id),
    status      TEXT NOT NULL DEFAULT 'suspected'
                CHECK (status IN ('suspected','verifying','confirmed','dismissed')),
    source_urls TEXT,                        -- JSON 数组：Retraction Watch / PubPeer / 新闻
    reported_at TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at TEXT,
    confirmed_by TEXT                        -- 定性必须人工
);

-- 论文级标记
CREATE TABLE IF NOT EXISTS paper_flags (
    id        INTEGER PRIMARY KEY,
    paper_id  INTEGER NOT NULL REFERENCES papers(id),
    event_id  INTEGER REFERENCES fraud_events(id),
    flag_type TEXT NOT NULL CHECK (flag_type IN ('retraction','correction','expression_of_concern','questioned')),
    note      TEXT,
    source_url TEXT,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pflags_paper ON paper_flags(paper_id);

-- 人员级标记：L0 确认造假（人工定性）/ L1 风险提示（自动，绝非定性）
CREATE TABLE IF NOT EXISTS author_flags (
    id        INTEGER PRIMARY KEY,
    author_id TEXT NOT NULL REFERENCES authors(id),
    event_id  INTEGER REFERENCES fraud_events(id),
    level     TEXT NOT NULL CHECK (level IN ('L0','L1')),
    basis     TEXT,                          -- 定性依据 / 关联说明（共著/引用）
    status    TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','dismissed')),
    confirmed_by TEXT,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_aflags_author ON author_flags(author_id);

-- 受影响的结论/快照修订
CREATE TABLE IF NOT EXISTS affected_snapshots (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES fraud_events(id),
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    action      TEXT NOT NULL DEFAULT 'pending_review'
                CHECK (action IN ('pending_review','revised','no_action')),
    decided_by  TEXT,
    decided_at  TEXT
);

-- ============ 审计留痕 ============
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL DEFAULT 'system',
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    detail      TEXT                         -- JSON
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);

-- ============ 增量采集游标 ============
CREATE TABLE IF NOT EXISTS cursors (
    domain_id TEXT NOT NULL,
    source    TEXT NOT NULL,                 -- pubmed / openalex / ...
    cursor_value TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (domain_id, source)
);

-- 待办队列视图：未校对汉字名
CREATE VIEW IF NOT EXISTS v_alias_review AS
SELECT a.id AS author_id, a.name_display, al.alias, al.alias_type, al.source, al.confidence
FROM author_aliases al JOIN authors a ON a.id = al.author_id
WHERE al.verified = 0 AND al.alias_type = 'hanzi';

-- ============ 定位调整增量（2026-08-27） ============

-- 作者级快照（LLM 合成画像 + 人工在环；与 snapshots 治理结构对齐）
CREATE TABLE IF NOT EXISTS author_snapshots (
    id          INTEGER PRIMARY KEY,
    author_id   TEXT NOT NULL REFERENCES authors(id),
    content     TEXT NOT NULL,              -- {focus, summary, key_contributions, risks, representative_paper_ids}
    model       TEXT,
    prompt_ver  TEXT,
    basis_signature TEXT,                   -- 合成时该作者论文集合指纹（失效感知）
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','affected_pending_review','superseded')),
    review_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (review_status IN ('pending','approved','rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    supersedes  INTEGER REFERENCES author_snapshots(id),
    generated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_author_snapshots_author ON author_snapshots(author_id);

-- LLM 判断裁决队列（噪声簇/方向并拆/别名候选）：LLM 提案 → 人工裁决 → 留痕
CREATE TABLE IF NOT EXISTS judgments (
    id          INTEGER PRIMARY KEY,
    jtype       TEXT NOT NULL CHECK (jtype IN ('noise_cluster','direction_merge','direction_split','alias_candidate')),
    entity_type TEXT NOT NULL,              -- cluster | author
    entity_id   TEXT NOT NULL,
    proposal    TEXT NOT NULL,              -- JSON：建议内容 + 理由 + 锚定 paper_id
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','accepted','rejected','superseded')),
    decided_by  TEXT,
    decided_at  TEXT,
    decision_note TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_judgments_status ON judgments(status);

-- B 层源（机构 webvpn：Scopus/CNKI/万方）导入批次：审计与去重
CREATE TABLE IF NOT EXISTS webvpn_imports (
    id          INTEGER PRIMARY KEY,
    domain_id   TEXT NOT NULL REFERENCES domains(id),
    source      TEXT NOT NULL CHECK (source IN ('scopus','cnki','wanfang')),
    file_name   TEXT,
    file_hash   TEXT,                       -- 导出文件指纹（防重复导入）
    query       TEXT,                       -- 检索式/来源说明
    n_records   INTEGER,
    n_new       INTEGER,                    -- 新增论文
    n_dup       INTEGER,                    -- 去重跳过
    status      TEXT NOT NULL DEFAULT 'ok',
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_webvpn_domain ON webvpn_imports(domain_id);

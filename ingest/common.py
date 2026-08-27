"""公共基础：数据库访问、HTTP 抓取（重试）、ID 生成、审计。仅标准库。"""
import gzip
import http.client
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "peopar.db"
SCHEMA_PATH = ROOT / "schema.sql"
CONFIG_DIR = ROOT / "config" / "domains"

UA = {"User-Agent": "peopar/0.1 (research-graph; mailto:zhangxinhao@local)"}

# 网络层可重试异常（断连/超时/响应不完整）
RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError,
             http.client.IncompleteRead, http.client.RemoteDisconnected)


def name_sort(name: str) -> str:
    """去音调规范化：Lesné → lesne，用于无重音检索。"""
    import unicodedata
    s = unicodedata.normalize("NFD", name or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # 轻量迁移：旧库补 name_sort 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(authors)")}
    if "name_sort" not in cols:
        conn.execute("ALTER TABLE authors ADD COLUMN name_sort TEXT")
    # 轻量迁移：旧库补快照审阅列
    scols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    for col, ddl in [
        ("review_status", "ALTER TABLE snapshots ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'"),
        ("prompt_ver", "ALTER TABLE snapshots ADD COLUMN prompt_ver TEXT"),
        ("reviewed_by", "ALTER TABLE snapshots ADD COLUMN reviewed_by TEXT"),
        ("reviewed_at", "ALTER TABLE snapshots ADD COLUMN reviewed_at TEXT"),
        ("basis_signature", "ALTER TABLE snapshots ADD COLUMN basis_signature TEXT"),
    ]:
        if col not in scols:
            conn.execute(ddl)
    # 轻量迁移：簇展示属性（噪声簇排除）
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(clusters)")}
    if "display" not in ccols:
        conn.execute("ALTER TABLE clusters ADD COLUMN display TEXT NOT NULL DEFAULT 'normal'")
    # 轻量迁移：author_clusters 按簇查询索引
    idx = {r[1] for r in conn.execute("PRAGMA index_list(author_clusters)")}
    if "ix_ac_cluster" not in idx:
        conn.execute("CREATE INDEX IF NOT EXISTS ix_ac_cluster ON author_clusters(cluster_id)")
    # 轻量迁移：机构官网抓取信息（note / verified）
    acols = {r[1] for r in conn.execute("PRAGMA table_info(affiliations)")}
    if "note" not in acols:
        conn.execute("ALTER TABLE affiliations ADD COLUMN note TEXT")
    if "verified" not in acols:
        conn.execute("ALTER TABLE affiliations ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def basis_signature(ids) -> str:
    """论文 id 集合的稳定指纹（排序拼接 sha256 前 16 位），用于快照失效感知。"""
    import hashlib
    key = ",".join(str(i) for i in sorted(ids))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def audit(conn, actor, action, entity_type=None, entity_id=None, detail=None):
    conn.execute(
        "INSERT INTO audit_log(actor, action, entity_type, entity_id, detail) VALUES(?,?,?,?,?)",
        (actor, action, entity_type, str(entity_id) if entity_id is not None else None,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None),
    )


def next_author_id(conn) -> str:
    row = conn.execute("SELECT id FROM authors ORDER BY id DESC LIMIT 1").fetchone()
    n = int(row["id"][2:]) + 1 if row else 1
    return f"BG{n:06d}"


# ---------- 规范化 ----------

def norm_title(title: str) -> str:
    """规范化标题：小写、去标点与空白，用于三级主键。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (title or "").lower())


def norm_pinyin(name: str) -> str:
    """归一化署名拼音：小写、去空白与标点。如 'Zhang XH' -> 'zhangxh'"""
    return re.sub(r"[^0-9a-z]+", "", (name or "").lower())


# ---------- HTTP ----------

def fetch_json(url: str, params: dict | None = None, retries: int = 3,
               timeout: int = 30, accept_gzip: bool = False):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return json.loads(data.decode("utf-8"), strict=False)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # 限流退避
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
                last_err = e
                continue
            if 500 <= e.code < 600 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                last_err = e
                continue
            raise
        except RETRYABLE as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch failed after {retries} retries: {url} :: {last_err}")


def fetch_xml(url: str, params: dict | None = None, retries: int = 3, timeout: int = 60):
    import xml.etree.ElementTree as ET
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return ET.fromstring(data)
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(2 ** attempt + 1)
                last_err = e
                continue
            raise
        except RETRYABLE as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch failed after {retries} retries: {url} :: {last_err}")


def load_domain_config(domain_id: str) -> dict:
    path = CONFIG_DIR / f"{domain_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_domain(conn, domain_id: str):
    cfg = load_domain_config(domain_id)
    conn.execute(
        "INSERT OR IGNORE INTO domains(id, name, description, config_path) VALUES(?,?,?,?)",
        (domain_id, cfg["name"], cfg.get("description", ""), str(CONFIG_DIR / f"{domain_id}.json")),
    )
    conn.commit()
    return cfg

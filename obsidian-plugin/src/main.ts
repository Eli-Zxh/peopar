import { App, Notice, Plugin, PluginSettingTab, Setting, TAbstractFile, TFile } from "obsidian";
import { ping, DataProvider } from "./api";
import { LiveProvider } from "./live";
import { VaultProvider } from "./vaultData";
import { AtlasView, PeoparFileView } from "./views";

export const VIEW_TYPE = "peopar-atlas";
export const FILE_VIEW_TYPE = "peopar-file";

interface PeoparSettings {
  enableServer: boolean;      // 实时服务（python3 app.py）；默认关闭——静态 vault 快照优先
  topic: string;              // 研究大方向名（vault 内 <topic>/peopar 目录）
  peoparDir: string;          // peopar 仓库根目录（enableServer 时 spawn 用）
  killServerOnUnload: boolean;
}
const DEFAULT_SETTINGS: PeoparSettings = {
  enableServer: false,
  topic: "神经语言学与失语症",
  peoparDir: "~/Documents/script/peopar",
  killServerOnUnload: false,
};

export default class PeoparPlugin extends Plugin {
  settings: PeoparSettings = { ...DEFAULT_SETTINGS };
  provider: DataProvider = new VaultProvider(this.app, DEFAULT_SETTINGS.topic);
  serverPid: number | null = null;
  private dataListeners: (() => void)[] = [];

  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());

    this.registerView(VIEW_TYPE, (leaf) => new AtlasView(leaf, this));
    // .peopar 扩展名文件 → 文件视图（frontmatter.domain 指定域），双击触发，避免误触发
    this.registerExtensions(["peopar"], FILE_VIEW_TYPE);
    this.registerView(FILE_VIEW_TYPE, (leaf) => new PeoparFileView(leaf, this));

    this.addRibbonIcon("network", "百官行述 · 研究者图谱", () => this.openView());
    this.addCommand({ id: "open-atlas", name: "打开百官行述图谱", callback: () => this.openView() });
    this.addCommand({ id: "enable-server", name: "开启实时服务（读取 SQLite 权威数据）", callback: async () => {
      this.settings.enableServer = true;
      await this.saveData(this.settings);
      await this.refreshProvider();
      new Notice(this.provider.serverConnected() ? "实时服务已连接" : "实时服务不可用（检查 python3 与仓库目录）");
    }});
    this.addCommand({ id: "disable-server", name: "切回 vault 快照模式（离线）", callback: async () => {
      this.settings.enableServer = false;
      await this.saveData(this.settings);
      this.refreshProvider();
      new Notice("已切回 vault 快照模式");
    }});
    this.addSettingTab(new PeoparSettingTab(this.app, this));

    // Obsidian 布局尺寸变化（侧栏/窗口）→ 视图图表真重绘
    this.registerEvent(this.app.workspace.on("resize", () => this.notifyChartResize()));
    // vault 文件监听：peopar/ 目录变化（skill/agent 重新导出）→ 通知所有打开的视图刷新
    this.registerEvent(this.app.vault.on("modify", (f) => this.onVaultChange(f)));
    this.registerEvent(this.app.vault.on("create", (f) => this.onVaultChange(f)));
    this.registerEvent(this.app.vault.on("delete", (f) => this.onVaultChange(f)));

    await this.refreshProvider();
  }

  private onVaultChange(f: TAbstractFile) {
    if (f instanceof TFile && f.path.startsWith("peopar/")) this.notifyDataChange();
  }

  onunload() {
    if (this.settings.killServerOnUnload && this.serverPid) this.stopServer();
  }

  private chartResize: (() => void)[] = [];
  onChartResize(fn: () => void): () => void {
    this.chartResize.push(fn);
    return () => { this.chartResize = this.chartResize.filter(x => x !== fn); };
  }
  private notifyChartResize() { this.chartResize.forEach(fn => { try { fn(); } catch {} }); }

  /** 视图注册数据变化回调，返回注销函数 */
  onDataChange(fn: () => void): () => void {
    this.dataListeners.push(fn);
    return () => {
      this.dataListeners = this.dataListeners.filter(x => x !== fn);
    };
  }
  private notifyDataChange() {
    this.dataListeners.forEach(fn => { try { fn(); } catch { /* ignore */ } });
  }

  /** 依据设置选择 provider：enableServer 且本地服务可达 → LiveProvider，否则 VaultProvider */
  async refreshProvider() {
    if (this.settings.enableServer) {
      if (await ping()) {
        this.provider = new LiveProvider();
      } else {
        if (this.serverPid === null) this.spawnServer();
        this.provider = new VaultProvider(this.app, this.settings.topic);
      }
    } else {
      this.provider = new VaultProvider(this.app, this.settings.topic);
    }
    this.notifyDataChange();
  }

  async openView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = workspace.getLeaf("tab");
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    workspace.revealLeaf(leaf);
  }

  expandHome(p: string): string {
    if (p.startsWith("~/")) {
      const home = require("os").homedir() as string;
      return home + p.slice(1);
    }
    return p;
  }

  private spawnServer() {
    try {
      const { spawn } = require("child_process") as typeof import("child_process");
      const dir = this.expandHome(this.settings.peoparDir);
      const child = spawn("python3", ["app.py"], { cwd: dir, detached: true, stdio: "ignore" });
      this.serverPid = child.pid ?? null;
      child.unref();
      // 轮询就绪
      (async () => {
        for (let i = 0; i < 40; i++) {
          await new Promise(r => setTimeout(r, 500));
          if (await ping()) { this.provider = new LiveProvider(); this.notifyDataChange(); return; }
        }
      })();
    } catch (e) {
      console.error("[peopar] spawn 失败", e);
    }
  }

  stopServer() {
    if (this.serverPid) {
      try { process.kill(this.serverPid); } catch { /* already gone */ }
      this.serverPid = null;
    }
  }
}

class PeoparSettingTab extends PluginSettingTab {
  plugin: PeoparPlugin;
  constructor(app: App, plugin: PeoparPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "百官行述 · 设置" });
    new Setting(containerEl)
      .setName("实时服务（SQLite 权威数据）")
      .setDesc("默认关闭：视图读 vault 内 peopar/ 快照（离线可用）。开启后探测/拉起 python3 app.py，提供实时数据与写操作（审阅/裁决/校对）。")
      .addToggle(t => t.setValue(this.plugin.settings.enableServer).onChange(async v => {
        this.plugin.settings.enableServer = v;
        await this.plugin.saveData(this.plugin.settings);
        await this.plugin.refreshProvider();
      }));
    new Setting(containerEl)
      .setName("研究大方向（vault 目录）")
      .setDesc("vault 内 <方向名>/peopar 目录，与 python3 manage/export_vault.py --topic 保持一致")
      .addText(t => t.setValue(this.plugin.settings.topic).onChange(async v => {
        this.plugin.settings.topic = v || "神经语言学与失语症";
        await this.plugin.saveData(this.plugin.settings);
        this.plugin.refreshProvider();
      }));
    new Setting(containerEl)
      .setName("peopar 仓库目录")
      .setDesc("python3 app.py 所在目录（仅实时服务模式使用）")
      .addText(t => t.setValue(this.plugin.settings.peoparDir).onChange(async v => {
        this.plugin.settings.peoparDir = v;
        await this.plugin.saveData(this.plugin.settings);
      }));
    new Setting(containerEl)
      .setName("插件停用时关闭本地服务")
      .setDesc("实时服务模式下手动拉起的进程是否随插件停用而终止")
      .addToggle(t => t.setValue(this.plugin.settings.killServerOnUnload).onChange(async v => {
        this.plugin.settings.killServerOnUnload = v;
        await this.plugin.saveData(this.plugin.settings);
      }));
    containerEl.createEl("p", {
      text: "数据更新：由 skill/agent 运行 python3 manage/export_vault.py \"vault路径\" 导出后，本插件通过 Obsidian 文件监听自动刷新视图。",
      cls: "setting-item-description",
    });
  }
}

import { ItemView, Notice, Plugin, WorkspaceLeaf } from "obsidian";
import { ping } from "./api";
import { AtlasView } from "./views";

export const VIEW_TYPE = "peopar-atlas";

interface PeoparSettings {
  peoparDir: string;          // peopar 仓库根目录（python3 app.py 所在）
  killServerOnUnload: boolean;
  autoStartServer: boolean;
}
const DEFAULT_SETTINGS: PeoparSettings = {
  peoparDir: "~/Documents/script/peopar",
  killServerOnUnload: false,
  autoStartServer: true,
};

export default class PeoparPlugin extends Plugin {
  settings: PeoparSettings = { ...DEFAULT_SETTINGS };
  serverPid: number | null = null;

  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());

    this.registerView(VIEW_TYPE, (leaf) => new AtlasView(leaf, this));

    this.addRibbonIcon("network", "百官行述 · 研究者图谱", () => this.openView());
    this.addCommand({ id: "open-atlas", name: "打开百官行述图谱", callback: () => this.openView() });
    this.addCommand({ id: "start-server", name: "启动本地数据服务", callback: async () => {
      const ok = await this.ensureServer();
      new Notice(ok ? "peopar 本地服务已就绪" : "peopar 服务启动失败（请检查 python3 与目录配置）");
    }});
    this.addCommand({ id: "stop-server", name: "停止本地数据服务", callback: () => {
      this.stopServer();
      new Notice("已停止 peopar 本地服务");
    }});
    this.addSettingTab(new PeoparSettingTab(this.app, this));

    if (this.settings.autoStartServer) {
      this.ensureServer().then(ok => {
        if (!ok) new Notice("peopar 本地服务未就绪：python3 app.py 未运行");
      });
    }
  }

  onunload() {
    if (this.settings.killServerOnUnload && this.serverPid) this.stopServer();
  }

  async openView() {
    if (this.settings.autoStartServer) await this.ensureServer();
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

  async ensureServer(): Promise<boolean> {
    if (await ping()) return true;
    try {
      const { spawn } = require("child_process") as typeof import("child_process");
      const dir = this.expandHome(this.settings.peoparDir);
      const child = spawn("python3", ["app.py"], {
        cwd: dir, detached: true, stdio: "ignore",
      });
      this.serverPid = child.pid ?? null;
      child.unref();
    } catch (e) {
      console.error("[peopar] spawn 失败", e);
      return false;
    }
    // 轮询就绪（最长 ~20s）
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 500));
      if (await ping()) return true;
    }
    return false;
  }

  stopServer() {
    if (this.serverPid) {
      try { process.kill(this.serverPid); } catch { /* already gone */ }
      this.serverPid = null;
    }
  }
}

import { App, PluginSettingTab, Setting } from "obsidian";
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
      .setName("peopar 仓库目录")
      .setDesc("python3 app.py 所在目录（本地服务与数据）")
      .addText(t => t.setValue(this.plugin.settings.peoparDir).onChange(async v => {
        this.plugin.settings.peoparDir = v;
        await this.plugin.saveData(this.plugin.settings);
      }));
    new Setting(containerEl)
      .setName("打开视图时自动启动本地服务")
      .setDesc("激活插件时探测 127.0.0.1:8765，未就绪则自动 spawn python3 app.py")
      .addToggle(t => t.setValue(this.plugin.settings.autoStartServer).onChange(async v => {
        this.plugin.settings.autoStartServer = v;
        await this.plugin.saveData(this.plugin.settings);
      }));
    new Setting(containerEl)
      .setName("插件停用时关闭本地服务")
      .setDesc("默认不关闭（服务可被浏览器版/命令行共享）；开启后停用插件即 kill 服务进程")
      .addToggle(t => t.setValue(this.plugin.settings.killServerOnUnload).onChange(async v => {
        this.plugin.settings.killServerOnUnload = v;
        await this.plugin.saveData(this.plugin.settings);
      }));
  }
}

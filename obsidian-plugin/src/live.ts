/** LiveProvider：通过本地 peopar 服务（127.0.0.1:8765）获取实时数据 + 写操作。 */
import {
  api, DataProvider, Domain, DirectionsResp, TrendsResp, DirectionResp, LayoutData,
  AuthorDetail, AuthorSnapshotResp, FraudEvent, FraudEventDetail,
} from "./api";

export class LiveProvider implements DataProvider {
  constructor(private base: string = "http://127.0.0.1:8765") {}
  serverConnected(): boolean { return true; }
  writeSupported(): boolean { return true; }
  lastSync(): string | null { return null; }

  domains() { return api<Domain[]>(this.base + "/api/domains"); }
  directions(domain: string) { return api<DirectionsResp>(this.base + `/api/directions?domain=${domain}`); }
  trends(domain: string) { return api<TrendsResp>(this.base + `/api/trends?domain=${domain}`); }
  layout(domain: string) { return api<LayoutData>(this.base + `/api/layout?domain=${domain}`); }
  directionResearchers(cid: number) { return api<DirectionResp>(this.base + `/api/direction/${cid}/researchers`); }
  author(id: string) { return api<AuthorDetail>(this.base + `/api/author/${id}`); }
  authorSnapshot(id: string) {
    return api<AuthorSnapshotResp | null>(this.base + `/api/author/${id}/snapshot`).catch(() => null);
  }
  events() { return api<FraudEvent[]>(this.base + "/api/events"); }
  eventDetail(id: number) { return api<FraudEventDetail>(this.base + `/api/event/${id}`); }
  search(q: string) { return api<any>(this.base + "/api/search?q=" + encodeURIComponent(q)); }
  /** 写操作透传（视图层仅在 writeSupported 时调用） */
  post(path: string, body: any) {
    return api<any>(this.base + path, { method: "POST", body: JSON.stringify(body) });
  }
  get(path: string) { return api<any>(this.base + path); }
}

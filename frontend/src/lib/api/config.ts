import apiClient from "./client";

/** UI-relevant runtime flags from GET /config. */
export interface AppConfig {
  /** Transport gate: false (default) = every email waits on a human decision. */
  allow_auto_send: boolean;
}

/** GET /config — runtime flags that shape the review UI. */
export async function getAppConfig(): Promise<AppConfig> {
  const { data } = await apiClient.get<AppConfig>("/config");
  return data;
}

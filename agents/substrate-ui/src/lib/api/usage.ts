import { API_BASE, ApiError, getErrorMessage, requestJson } from "./_client";

export interface DayUsage {
  date: string;
  messages: number;
  calls: number;
  tokens: number;
  cost_usd: number;
}

export interface Totals {
  messages: number;
  tokens: number;
  cost_usd: number;
}

export interface Usage {
  days: DayUsage[];
  today: Totals;
  this_month: Totals;
  window: Totals;
  note: string;
}

export const usageApi = {
  async getUsage(days = 30): Promise<Usage> {
    return requestJson<Usage>(`/me/usage?days=${days}`);
  },

  /** Download everything the account holds about the user as one JSON file. */
  async downloadMyData(): Promise<void> {
    const res = await fetch(`${API_BASE}/me/export`, { credentials: "include" });
    if (!res.ok) throw new ApiError(await getErrorMessage(res, "Couldn't export your data"), res.status);
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "my-data.json";
    link.click();
    URL.revokeObjectURL(url);
  },
};

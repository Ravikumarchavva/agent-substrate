import { requestJson } from "./_client";

/** What the account remembers about how the user wants things (see `lib/preferences-sync.ts`). */
export interface Preferences {
  custom_instructions: string;
  timezone: string;
  models: Record<string, string>;
}

export const preferencesApi = {
  async getPreferences(): Promise<Preferences> {
    return requestJson<Preferences>("/me/preferences");
  },

  async putPreferences(preferences: Preferences): Promise<Preferences> {
    return requestJson<Preferences>("/me/preferences", { method: "PUT", body: JSON.stringify(preferences) });
  },
};

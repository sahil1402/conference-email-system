import { useQuery } from "@tanstack/react-query";

import { getAppConfig } from "@/lib/api";

/**
 * App runtime flags (e.g. allow_auto_send). Cached indefinitely — these change
 * only on a backend restart. Defaults to the SAFE value (auto-send OFF → every
 * email is human-gated) until loaded or if the request fails, so the UI never
 * shows "auto-replied" on a guess.
 */
export function useAppConfig() {
  const { data } = useQuery({
    queryKey: ["appConfig"],
    queryFn: getAppConfig,
    staleTime: Infinity,
  });
  return { allowAutoSend: data?.allow_auto_send ?? false };
}

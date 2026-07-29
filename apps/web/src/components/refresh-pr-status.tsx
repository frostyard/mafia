"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { refreshRun } from "@/lib/api";
import type { ApiError } from "@/lib/types";

export function RefreshPrStatus({ runId }: { runId: string }) {
  const router = useRouter();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string>();

  async function refresh() {
    setError(undefined);
    setIsRefreshing(true);
    try {
      await refreshRun(runId);
      router.refresh();
    } catch (requestError) {
      setError((requestError as ApiError).message ?? "Unable to refresh pull request status.");
    } finally {
      setIsRefreshing(false);
    }
  }

  return (
    <div className="refresh-control">
      <button className="button button-secondary" disabled={isRefreshing} onClick={refresh} type="button">
        {isRefreshing ? "Refreshing..." : "Refresh PR status"}
      </button>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

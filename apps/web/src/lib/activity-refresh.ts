import type { RunActivity } from "@/lib/types";

function structuralSignature(activity: RunActivity): string {
  return [activity.state, activity.version].join("|");
}

export function shouldRefreshRunPage(
  previous: RunActivity,
  next: RunActivity,
): boolean {
  if (structuralSignature(previous) === structuralSignature(next)) return false;
  return next.status_mode !== "working";
}

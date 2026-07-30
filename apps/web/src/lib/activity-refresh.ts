import type { RunActivity } from "@/lib/types";

function structuralSignature(activity: RunActivity): string {
  return [activity.state, activity.version].join("|");
}

function completedArtifactOperations(activity: RunActivity): Set<string> {
  return new Set(
    activity.operations
      .filter(
        (operation) =>
          operation.operation_type === "artifact.persistence" &&
          operation.status === "completed",
      )
      .map((operation) => operation.id),
  );
}

export function shouldRefreshRunPage(
  previous: RunActivity,
  next: RunActivity,
): boolean {
  const previousArtifacts = completedArtifactOperations(previous);
  const artifactCompleted = next.operations.some(
    (operation) =>
      operation.operation_type === "artifact.persistence" &&
      operation.status === "completed" &&
      !previousArtifacts.has(operation.id),
  );
  if (artifactCompleted) return true;
  if (structuralSignature(previous) === structuralSignature(next)) return false;
  return next.status_mode !== "working";
}

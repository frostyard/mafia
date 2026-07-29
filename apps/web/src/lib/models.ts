import type { ModelPair, PrimaryModel } from "@/lib/types";

export function formatModelName(identifier: string): string {
  const parts = identifier.split("-");
  if (parts[0]?.toLowerCase() === "gpt" && parts.length > 1) {
    return [
      `GPT-${parts[1]}`,
      ...parts.slice(2).map(capitalize),
    ].join(" ");
  }
  return parts.map(capitalize).join(" ");
}

function capitalize(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

export function reviewerFor(
  primaryModel: PrimaryModel,
  pairs: ModelPair[],
): string {
  const reviewer = pairs.find(
    (pair) => pair.primary_model === primaryModel,
  )?.reviewer_model;
  return reviewer ? formatModelName(reviewer) : "Independent reviewer";
}

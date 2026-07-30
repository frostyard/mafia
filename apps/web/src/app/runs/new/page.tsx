import { RunForm } from "@/components/run-form";
import { getModelAvailability } from "@/lib/api";
import type { ApiError, ModelAvailability } from "@/lib/types";

export const metadata = { title: "New run" };

export default async function NewRunPage() {
  let modelAvailability: ModelAvailability | undefined;
  let modelLoadError: string | undefined;
  try {
    modelAvailability = await getModelAvailability();
  } catch (error) {
    modelLoadError =
      (error as ApiError).message ?? "Model availability could not be loaded.";
  }
  return (
    <>
      <header className="ph-topbar">
        <div>
          <p className="ph-eyebrow">Workflows · configure</p>
          <h1>New engineering run</h1>
        </div>
      </header>
      <RunForm
        modelAvailability={modelAvailability}
        modelLoadError={modelLoadError}
      />
    </>
  );
}

import { RunForm } from "@/components/run-form";
import { getModelAvailability } from "@/lib/api";

export const metadata = { title: "New run" };

export default async function NewRunPage() {
  let modelAvailability;
  try {
    modelAvailability = await getModelAvailability();
  } catch {}
  return (
    <>
      <header className="ph-topbar">
        <div>
          <p className="ph-eyebrow">Workflows · configure</p>
          <h1>New engineering run</h1>
        </div>
      </header>
      <RunForm modelAvailability={modelAvailability} />
    </>
  );
}

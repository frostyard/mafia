import Link from "next/link";
import { notFound } from "next/navigation";
import { ProjectSettingsForm } from "@/components/project-settings-form";
import { getProject } from "@/lib/api";

export const metadata = { title: "Project settings" };

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let project;
  try {
    project = await getProject(id);
  } catch {
    notFound();
  }
  return (
    <>
      <header className="ph-topbar">
        <div>
          <p className="ph-eyebrow">{project.owner} · project</p>
          <h1>{project.name}</h1>
        </div>
        <Link className="button button-secondary button-small" href="/projects">
          All projects
        </Link>
      </header>
      <ProjectSettingsForm project={project} />
    </>
  );
}

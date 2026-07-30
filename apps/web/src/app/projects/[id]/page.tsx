import Link from "next/link";
import { notFound } from "next/navigation";
import { ProjectSettingsForm } from "@/components/project-settings-form";
import { getProject } from "@/lib/api";
import type { ApiError, Project } from "@/lib/types";

export const metadata = { title: "Project settings" };

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let project: Project | undefined;
  let requestError: ApiError | undefined;
  try {
    project = await getProject(id);
  } catch (error) {
    requestError = error as ApiError;
  }
  if (requestError?.code === "project_not_found") {
    notFound();
  }
  if (!project) {
    return (
      <section className="empty-state ph-card" role="status">
        <p className="eyebrow">Project unavailable</p>
        <h1>Project settings are unavailable</h1>
        <p className="muted">
          {requestError?.message ?? "Try again after the API is available."}
        </p>
        <a className="button" href={`/projects/${id}`}>
          Try again
        </a>
      </section>
    );
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

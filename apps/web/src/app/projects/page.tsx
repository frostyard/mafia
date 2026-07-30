import { ProjectList } from "@/components/project-list";
import { getProjects } from "@/lib/api";
import type { ApiError, Project } from "@/lib/types";

export const metadata = { title: "Projects" };

export default async function ProjectsPage() {
  let projects: Project[] | undefined;
  let requestError: ApiError | undefined;
  try {
    projects = await getProjects();
  } catch (error) {
    requestError = error as ApiError;
  }
  if (!projects) {
    return (
      <section className="empty-state ph-card" role="status">
        <p className="eyebrow">Projects unavailable</p>
        <h1>Project settings are unavailable</h1>
        <p className="muted">
          {requestError?.message ?? "Try again after the API is available."}
        </p>
        {/* A document navigation is required to retry the failed server request. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a className="button" href="/projects">
          Try again
        </a>
      </section>
    );
  }
  return (
    <>
      <header className="ph-topbar">
        <div>
          <p className="ph-eyebrow">Projects · configuration</p>
          <h1>Project settings</h1>
        </div>
      </header>
      <ProjectList projects={projects} />
    </>
  );
}

import { ProjectList } from "@/components/project-list";
import { getProjects } from "@/lib/api";

export const metadata = { title: "Projects" };

export default async function ProjectsPage() {
  const projects = await getProjects();
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

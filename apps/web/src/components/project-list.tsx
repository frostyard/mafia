"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { createProject } from "@/lib/api";
import type { ApiError, Project } from "@/lib/types";

export function ProjectList({ projects }: { projects: Project[] }) {
  const router = useRouter();
  const [repository, setRepository] = useState("");
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setSubmitting(true);
    try {
      const project = await createProject(repository);
      router.push(`/projects/${project.id}`);
    } catch (caught) {
      setError((caught as ApiError).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="project-layout">
      <form className="ph-card project-create" onSubmit={submit}>
        <div className="field">
          <label htmlFor="project-repository">Add project</label>
          <input
            id="project-repository"
            placeholder="owner/repository"
            value={repository}
            onChange={(event) => setRepository(event.target.value)}
          />
          <p className="field-help">
            {error ?? "Create host-owned settings before starting a workflow."}
          </p>
        </div>
        <button className="button" disabled={submitting || !repository.trim()}>
          {submitting ? "Adding..." : "Add project"}
        </button>
      </form>
      <section className="project-grid" aria-label="Configured projects">
        {projects.map((project) => (
          <Link className="ph-card project-card" href={`/projects/${project.id}`} key={project.id}>
            <p className="eyebrow">{project.owner}</p>
            <h2>{project.name}</h2>
            <p className="muted">
              {project.execution_mode} execution ·{" "}
              {project.validation_commands.length
                ? `${project.validation_commands.length} validation command(s)`
                : "no host validation fallback"}
            </p>
          </Link>
        ))}
      </section>
    </div>
  );
}

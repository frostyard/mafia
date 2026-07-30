"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { updateProjectConfiguration } from "@/lib/api";
import type { ApiError, Project, ValidationCommand } from "@/lib/types";

function quote(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll("\"", "\\\"")
    .replaceAll("\n", "\\n");
}

function renderToml(mode: Project["execution_mode"], commands: ValidationCommand[]): string {
  const lines = ["version = 1", "", "[execution]", `mode = "${mode}"`];
  for (const command of commands) {
    lines.push(
      "",
      "[[validation.commands]]",
      `name = "${quote(command.name)}"`,
      `run = "${quote(command.run)}"`,
      `working_directory = "${quote(command.working_directory)}"`,
      `timeout_seconds = ${command.timeout_seconds}`,
    );
  }
  return `${lines.join("\n")}\n`;
}

const emptyCommand: ValidationCommand = {
  name: "",
  run: "",
  working_directory: ".",
  timeout_seconds: 900,
};

export function ProjectSettingsForm({ project }: { project: Project }) {
  const router = useRouter();
  const [mode, setMode] = useState(project.execution_mode);
  const [commands, setCommands] = useState(project.validation_commands);
  const generatedToml = useMemo(() => renderToml(mode, commands), [mode, commands]);
  const [tomlOverride, setTomlOverride] = useState<string>();
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const content = tomlOverride ?? generatedToml;

  function updateCommand(index: number, change: Partial<ValidationCommand>) {
    setCommands((current) =>
      current.map((command, commandIndex) =>
        commandIndex === index ? { ...command, ...change } : command
      ),
    );
    setTomlOverride(undefined);
    setSaved(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setSaved(false);
    setSubmitting(true);
    try {
      const updated = await updateProjectConfiguration(project.id, content);
      setMode(updated.execution_mode);
      setCommands(updated.validation_commands);
      setSaved(true);
      setTomlOverride(undefined);
      router.refresh();
    } catch (caught) {
      setError((caught as ApiError).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="ph-card project-settings" onSubmit={submit}>
      <fieldset className="field">
        <legend>Execution environment</legend>
        <div className="segmented-control">
          <label>
            <input
              type="radio"
              checked={mode === "isolated"}
              onChange={() => {
                setMode("isolated");
                setTomlOverride(undefined);
                setSaved(false);
              }}
            />
            Isolated
          </label>
          <label>
            <input
              type="radio"
              checked={mode === "host"}
              onChange={() => {
                setMode("host");
                setTomlOverride(undefined);
                setSaved(false);
              }}
            />
            Host
          </label>
        </div>
        <p className="field-help">
          Host mode runs project and agent commands directly on the mafia host. Use it only for trusted projects.
        </p>
      </fieldset>

      <div className="project-command-heading">
        <div>
          <p className="eyebrow">Mechanical gate</p>
          <h2>Validation commands</h2>
        </div>
        <button
          className="button button-secondary button-small"
          type="button"
          onClick={() => {
            setCommands((current) => [...current, { ...emptyCommand }]);
            setTomlOverride(undefined);
          }}
        >
          Add command
        </button>
      </div>

      {commands.length === 0 ? (
        <p className="form-alert">
          No host validation fallback is configured. Implementation requires a
          repository .mafia.toml.
        </p>
      ) : null}
      {commands.map((command, index) => (
        <fieldset className="project-command" key={index}>
          <div className="field">
            <label htmlFor={`command-name-${index}`}>Name</label>
            <input
              id={`command-name-${index}`}
              value={command.name}
              onChange={(event) => updateCommand(index, { name: event.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={`command-run-${index}`}>Command</label>
            <input
              id={`command-run-${index}`}
              value={command.run}
              onChange={(event) => updateCommand(index, { run: event.target.value })}
            />
          </div>
          <div className="project-command-row">
            <div className="field">
              <label htmlFor={`command-directory-${index}`}>Working directory</label>
              <input
                id={`command-directory-${index}`}
                value={command.working_directory}
                onChange={(event) => updateCommand(index, { working_directory: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor={`command-timeout-${index}`}>Timeout (seconds)</label>
              <input
                id={`command-timeout-${index}`}
                type="number"
                min={1}
                max={3600}
                value={command.timeout_seconds}
                onChange={(event) =>
                  updateCommand(index, { timeout_seconds: Number(event.target.value) })
                }
              />
            </div>
          </div>
          <button
            className="button button-quiet button-small"
            type="button"
            onClick={() => {
              setCommands((current) => current.filter((_, commandIndex) => commandIndex !== index));
              setTomlOverride(undefined);
            }}
          >
            Remove
          </button>
        </fieldset>
      ))}

      <details className="project-toml">
        <summary>Import, preview, or export TOML</summary>
        <div className="field">
          <label htmlFor="project-toml">Host .mafia.toml</label>
          <textarea
            id="project-toml"
            rows={14}
            value={content}
            onChange={(event) => {
              setTomlOverride(event.target.value);
              setSaved(false);
            }}
          />
          <p className="field-help">
            Pasted TOML is validated and normalized when saved.
          </p>
        </div>
        <a
          className="button button-secondary button-small"
          download={`${project.owner}-${project.name}.mafia.toml`}
          href={`data:application/toml;charset=utf-8,${encodeURIComponent(content)}`}
        >
          Download TOML
        </a>
      </details>

      {error ? <p className="form-alert" role="alert">{error}</p> : null}
      {saved ? <p className="field-help" role="status">Project settings saved.</p> : null}
      <div className="form-actions">
        <button className="button" disabled={submitting}>
          {submitting ? "Saving..." : "Save project settings"}
        </button>
      </div>
    </form>
  );
}

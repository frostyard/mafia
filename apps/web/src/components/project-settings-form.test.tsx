import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectSettingsForm } from "@/components/project-settings-form";

const { updateProjectConfiguration } = vi.hoisted(() => ({
  updateProjectConfiguration: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ updateProjectConfiguration }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

describe("ProjectSettingsForm", () => {
  const project = {
    id: "project-1",
    owner: "octo",
    name: "repo",
    remote_url: "https://github.com/octo/repo.git",
    default_branch: "main",
    configured: false,
    configuration_content: "",
    execution_mode: "isolated" as const,
    validation_commands: [],
  };

  beforeEach(() => {
    updateProjectConfiguration.mockReset();
  });

  it("builds host TOML from structured project settings", () => {
    render(
      <ProjectSettingsForm
        project={{
          id: "project-1",
          owner: "octo",
          name: "repo",
          remote_url: "https://github.com/octo/repo.git",
          default_branch: "main",
          configured: false,
          configuration_content: "",
          execution_mode: "isolated",
          validation_commands: [],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add command" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Repository checks" },
    });
    fireEvent.change(screen.getByLabelText("Command"), {
      target: { value: "npm run check" },
    });
    fireEvent.click(screen.getByText("Import, preview, or export TOML"));

    expect((screen.getByLabelText("Host .mafia.toml") as HTMLTextAreaElement).value)
      .toContain('run = "npm run check"');
  });

  it("preserves pasted TOML until the explicit discard action", () => {
    render(<ProjectSettingsForm project={project} />);

    fireEvent.click(screen.getByText("Import, preview, or export TOML"));
    fireEvent.change(screen.getByLabelText("Host .mafia.toml"), {
      target: { value: "version = 1\n# custom\n" },
    });

    expect(screen.getByLabelText("Isolated").closest("fieldset")).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Add command" })).toHaveProperty("disabled", true);
    expect(screen.getByDisplayValue(/# custom/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Discard raw TOML and edit fields" }));

    expect(screen.getByLabelText("Isolated").closest("fieldset")).toHaveProperty("disabled", false);
    expect(screen.queryByDisplayValue(/# custom/)).toBeNull();
  });

  it("keeps the normalized response in raw mode after saving TOML", async () => {
    updateProjectConfiguration.mockResolvedValue({
      ...project,
      execution_mode: "host",
      validation_commands: [],
    });
    render(<ProjectSettingsForm project={project} />);
    fireEvent.click(screen.getByText("Import, preview, or export TOML"));
    fireEvent.change(screen.getByLabelText("Host .mafia.toml"), {
      target: { value: "version = 1\n# custom\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save project settings" }));

    await waitFor(() => {
      expect(updateProjectConfiguration).toHaveBeenCalledWith("project-1", "version = 1\n# custom\n");
    });
    expect((screen.getByLabelText("Host .mafia.toml") as HTMLTextAreaElement).value)
      .toContain('mode = "host"');
    expect(screen.getByLabelText("Isolated").closest("fieldset")).toHaveProperty("disabled", true);
  });

  it.each(["", "0", "3601", "1.5", "invalid"])("rejects timeout %s before submission", async (value) => {
    render(<ProjectSettingsForm project={project} />);
    fireEvent.click(screen.getByRole("button", { name: "Add command" }));
    fireEvent.change(screen.getByLabelText("Timeout (seconds)"), {
      target: { value },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save project settings" }));

    await waitFor(() => expect(updateProjectConfiguration).not.toHaveBeenCalled());
    expect(screen.getByRole("alert").textContent).toContain("whole number from 1 to 3600");
    expect(screen.getByLabelText("Timeout (seconds)").getAttribute("aria-invalid")).toBe("true");
  });

  it("validates every command timeout before submission", async () => {
    render(<ProjectSettingsForm project={project} />);
    fireEvent.click(screen.getByRole("button", { name: "Add command" }));
    fireEvent.click(screen.getByRole("button", { name: "Add command" }));
    fireEvent.change(screen.getAllByLabelText("Timeout (seconds)")[1], {
      target: { value: "3601" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save project settings" }));

    await waitFor(() => expect(updateProjectConfiguration).not.toHaveBeenCalled());
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getAllByLabelText("Timeout (seconds)")[1].getAttribute("aria-invalid")).toBe("true");
  });
});

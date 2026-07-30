import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectSettingsForm } from "@/components/project-settings-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

describe("ProjectSettingsForm", () => {
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
});

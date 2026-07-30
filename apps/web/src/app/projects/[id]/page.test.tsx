import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectPage from "./page";
import { notFound } from "next/navigation";
import { getProject } from "@/lib/api";

vi.mock("next/navigation", () => ({ notFound: vi.fn() }));
vi.mock("@/components/project-settings-form", () => ({
  ProjectSettingsForm: () => null,
}));
vi.mock("@/lib/api", () => ({ getProject: vi.fn() }));

describe("ProjectPage", () => {
  beforeEach(() => {
    vi.mocked(getProject).mockReset();
    vi.mocked(notFound).mockReset();
  });

  it("only invokes notFound for the backend project-not-found error", async () => {
    vi.mocked(getProject).mockRejectedValue({
      code: "project_not_found",
      message: "missing-project",
    });

    await ProjectPage({ params: Promise.resolve({ id: "missing-project" }) });

    expect(notFound).toHaveBeenCalledOnce();
  });

  it("renders an unavailable state for project loading errors", async () => {
    vi.mocked(getProject).mockRejectedValue({ message: "Service unavailable" });

    render(await ProjectPage({ params: Promise.resolve({ id: "project-1" }) }));

    expect(
      screen.getByRole("heading", { name: "Project settings are unavailable" }),
    ).toBeTruthy();
    expect(notFound).not.toHaveBeenCalled();
    const retry = screen.getByRole("link", { name: "Try again" });
    expect(retry.tagName).toBe("A");
    expect(retry.getAttribute("href")).toBe("/projects/project-1");
  });
});

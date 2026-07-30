import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectsPage from "./page";
import { getProjects } from "@/lib/api";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/components/project-list", () => ({ ProjectList: () => null }));
vi.mock("@/lib/api", () => ({ getProjects: vi.fn() }));

describe("ProjectsPage", () => {
  beforeEach(() => {
    vi.mocked(getProjects).mockReset();
  });

  it("renders an unavailable state when project settings cannot load", async () => {
    vi.mocked(getProjects).mockRejectedValue({ message: "Service unavailable" });

    render(await ProjectsPage());

    expect(
      screen.getByRole("heading", { name: "Project settings are unavailable" }),
    ).toBeTruthy();
    expect(screen.getByText("Service unavailable")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try again" }).getAttribute("href")).toBe("/projects");
  });
});

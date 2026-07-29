type LandingPoint = {
  title: string;
  description: string;
};

type SiteConfig = {
  name: string;
  kicker: string;
  sourceUrl: string;
  url: string;
  landing: {
    headline: readonly [string, string];
    description: string;
    points: readonly [LandingPoint, LandingPoint, LandingPoint];
  };
};

export const site = {
  /** Project name - sidebar label + topbar crumb. Lowercase per brand. */
  name: "mafia",
  /** One-line descriptor under the name in the sidebar and landing eyebrow. */
  kicker: "Source-grounded engineering workflows",
  /** Source links in the top bar and landing hero. */
  sourceUrl: "https://github.com/frostyard/mafia",
  /** Canonical site URL (sitemap, astro `site`). */
  url: "https://mafia.frostyard.org",
  /** Project-specific root-page copy. Keep each value concise. */
  landing: {
    headline: ["Requirements go in.", "Reviewed changes come out."],
    description: "mafia turns GitHub issues and written requirements into source-grounded specifications, reviewed plans, and operator-approved pull requests.",
    points: [
      {
        title: "Grounded in source",
        description: "Every specification, plan, and review is tied to an exact repository revision and cited evidence."
      },
      {
        title: "Two models deliberate",
        description: "A configurable model pair separates generation, adversarial review, and final adjudication."
      },
      {
        title: "You control mutations",
        description: "Durable approval gates stop before implementation, pull requests, and published review comments."
      }
    ]
  }
} satisfies SiteConfig;

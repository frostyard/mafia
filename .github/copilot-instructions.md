# Repository instructions

Keep user-facing documentation synchronized with behavior and configuration changes.

- Treat `docs/` as the detailed repository documentation and `site/content/` as its navigable documentation-site counterpart. Update both when a change affects setup, configuration, workflow behavior, authentication, execution, deployment, or operations.
- Keep the README concise. Link to the detailed documentation instead of moving full operational guidance back into it.
- Maintain these topic mappings:
  - `docs/workflow.md` -> `site/content/workflows/`
  - `docs/execution-environments.md` -> `site/content/operations/execution-environments.md`
  - `docs/deployment.md` -> `site/content/operations/deployment.md`
  - `docs/incus.md` -> `site/content/operations/incus.md`
  - `docs/authentication.md` -> `site/content/operations/authentication.md`
  - `docs/development.md` -> `site/content/getting-started/installation.md` and `site/content/reference/development.md`
  - `.env.example` and `apps/api/src/mafia/config.py` -> `site/content/reference/configuration.md`
- Update screenshot assets and their alt text under `site/public/images/app/` when a visible interface change makes an existing screenshot misleading. Use sanitized disposable data for captures.
- Preserve sentence-case headings and lowercase `mafia` in site prose.
- Run `npm run check:site` after changing site content, navigation, styling, or assets.

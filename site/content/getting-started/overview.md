---
title: Overview
description: What mafia does, where it draws boundaries, and where to begin.
group: Getting started
order: 1
---

mafia is a source-grounded engineering workflow for specification-driven delivery and ad-hoc pull request review. It runs locally or as a private deployment using the operator's GitHub and Copilot identities.

<figure class="dk-screenshot">
  <img src="/images/app/run-dashboard.webp" alt="mafia workflow dashboard showing specification, pull request review, and completed runs" width="1536" height="960" />
  <figcaption>The workflow dashboard keeps active decisions, completed work, model pairs, and durable run revisions visible.</figcaption>
</figure>

## What this is

Start with a GitHub issue, a written requirement, or an existing pull request:

- A requirement becomes a structured specification, a source-grounded plan, and dependency-ordered pull request phases.
- A pull request receives two independent reviews and one adjudicated result.
- Every mutation waits behind an explicit operator decision.

Durable state, artifacts, source evidence, model operations, and approvals survive process restarts.

<figure class="dk-screenshot">
  <img src="/images/app/new-run.webp" alt="New mafia run form with workflow, repository, source, and primary model controls" width="1536" height="1000" loading="lazy" />
  <figcaption>A run begins with one workflow, repository, source input, and configured model pair.</figcaption>
</figure>

## Where the boundaries sit

Models inspect source and propose work. Deterministic application code owns repository refreshes, worktrees, validation, commits, pushes, pull request creation, comment publication, and merge reconciliation.

Implementation commands run in a Dev Container, a rootless bubblewrap sandbox, or an explicitly trusted host environment. GitHub and Copilot credentials remain outside isolated execution environments.

## Where to start

- Follow [installation](/getting-started/installation/) for a local source checkout.
- Read [specification delivery](/workflows/specification-delivery/) for the phased implementation path.
- Read [pull request review](/workflows/pull-request-review/) for ad-hoc review and publication.

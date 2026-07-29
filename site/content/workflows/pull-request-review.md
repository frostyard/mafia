---
title: Pull request review
description: Run two independent reviews and adjudicate one publishable result.
group: Workflows
order: 11
---

Select **Review pull request**, enter a repository and pull request number or URL, and choose the adjudicator model.

## Ground the review

mafia fetches the pull request's exact base and head commits and creates a read-only analysis worktree at the head. It resolves the merge base and validates findings only against lines changed by the pull request.

Repository content, pull request text, and review output remain untrusted input.

## Review independently

Both configured models inspect the source and unified diff independently. Findings include severity, category, path, old or new side, changed line range, evidence, and a suggested fix.

The selected adjudicator then:

1. Inspects both independent reviews.
2. Records a disposition for every proposed finding.
3. Merges duplicate findings.
4. Rejects claims that are unsupported or outside changed lines.
5. Persists one consolidated review.

<figure class="dk-screenshot">
  <img src="/images/app/pull-request-review.webp" alt="Pull request review run awaiting publication with independent review steps, source metrics, and consolidated findings" width="1680" height="1050" loading="lazy" />
  <figcaption>The review workspace exposes both model passes, adjudication progress, consolidated findings, and the final publication gate.</figcaption>
</figure>

## Decide whether to publish

The workflow pauses before any GitHub mutation.

- **Post to pull request** publishes the consolidated Markdown as an idempotent pull request comment.
- **Finish without posting** retains the local artifact only.

Model tools cannot post comments directly. Deterministic application code owns publication and prevents duplicate comments after retries.

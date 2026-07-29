"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { createRun } from "@/lib/api";
import { formatModelName, reviewerFor } from "@/lib/models";
import { validateRunForm, type RequirementMode, type RunFormValues } from "@/lib/validation";
import type { ApiError, ModelAvailability, PrimaryModel } from "@/lib/types";

const initialValues: RunFormValues = {
  workflowType: "specification",
  repository: "",
  primaryModel: "",
  requirementMode: "issue",
  issueNumber: "",
  requirementText: "",
  pullRequestNumber: "",
};

export function RunForm({
  modelAvailability,
}: {
  modelAvailability?: ModelAvailability;
}) {
  const router = useRouter();
  const modelPairs = modelAvailability?.pairs ?? [];
  const unavailableModels = new Set(modelAvailability?.missing ?? []);
  const pairIsAvailable = (primaryModel: string) => {
    const pair = modelPairs.find(
      (candidate) => candidate.primary_model === primaryModel,
    );
    return (
      pair !== undefined &&
      !unavailableModels.has(pair.primary_model) &&
      !unavailableModels.has(pair.reviewer_model)
    );
  };
  const firstAvailable = modelPairs.find((pair) =>
    pairIsAvailable(pair.primary_model)
  )?.primary_model;
  const [values, setValues] = useState({
    ...initialValues,
    primaryModel: firstAvailable ?? initialValues.primaryModel,
  });
  const [errors, setErrors] = useState<
    Partial<Record<keyof RunFormValues, string>>
  >({});
  const [submitError, setSubmitError] = useState<string>();
  const [isSubmitting, setIsSubmitting] = useState(false);

  function update<K extends keyof RunFormValues>(key: K, value: RunFormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(undefined);
    if (!pairIsAvailable(values.primaryModel)) {
      setErrors({ primaryModel: "This model pair is currently unavailable." });
      return;
    }
    const result = validateRunForm(values);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }

    setIsSubmitting(true);
    try {
      const run = await createRun(result.data);
      router.push(`/runs/${run.id}`);
    } catch (error) {
      const apiError = error as ApiError;
      setSubmitError(apiError.message ?? "Unable to create this run.");
    } finally {
      setIsSubmitting(false);
    }
  }

  const reviewer = reviewerFor(values.primaryModel, modelPairs);
  const adjudicator = values.primaryModel
    ? formatModelName(values.primaryModel)
    : "The adjudicator";
  const noModelsAvailable =
    firstAvailable === undefined;

  return (
    <form className="run-form ph-card" onSubmit={onSubmit} noValidate>
      <div className="form-heading">
        <div>
          <p className="eyebrow">Configure workflow</p>
          <h1>Start a new run</h1>
          <p className="muted">
            {values.workflowType === "pull_request_review"
              ? "Run two independent reviews and adjudicate one consolidated result."
              : "Turn one issue or requirement into an approved pull request."}
          </p>
        </div>
      </div>

      {submitError ? (
        <p className="form-alert" role="alert">
          {submitError}
        </p>
      ) : null}

      <fieldset className="field">
        <legend>Workflow</legend>
        <div className="segmented-control">
          <label>
            <input
              type="radio"
              name="workflowType"
              checked={values.workflowType === "specification"}
              onChange={() => update("workflowType", "specification")}
            />
            Build from requirement
          </label>
          <label>
            <input
              type="radio"
              name="workflowType"
              checked={values.workflowType === "pull_request_review"}
              onChange={() => update("workflowType", "pull_request_review")}
            />
            Review pull request
          </label>
        </div>
      </fieldset>

      <div className="field">
        <label htmlFor="repository">Repository</label>
        <input
          id="repository"
          name="repository"
          autoComplete="url"
          aria-describedby={errors.repository ? "repository-error" : "repository-help"}
          aria-invalid={Boolean(errors.repository)}
          placeholder="github.com/owner/repository"
          value={values.repository}
          onChange={(event) => update("repository", event.target.value)}
        />
        <p id={errors.repository ? "repository-error" : "repository-help"} className="field-help">
          {errors.repository ?? "Use a clone URL, GitHub URL, or owner/repository."}
        </p>
      </div>

      {values.workflowType === "specification" ? (
        <fieldset className="field">
          <legend>Source</legend>
          <div className="segmented-control">
            <label>
              <input
                type="radio"
                name="requirementMode"
                checked={values.requirementMode === "issue"}
                onChange={() => update("requirementMode", "issue" as RequirementMode)}
              />
              GitHub issue
            </label>
            <label>
              <input
                type="radio"
                name="requirementMode"
                checked={values.requirementMode === "text"}
                onChange={() => update("requirementMode", "text" as RequirementMode)}
              />
              Written requirement
            </label>
          </div>
        </fieldset>
      ) : null}

      {values.workflowType === "pull_request_review" ? (
        <div className="field">
          <label htmlFor="pull-request-number">Pull request number or URL</label>
          <input
            id="pull-request-number"
            name="pullRequestNumber"
            type="text"
            aria-invalid={Boolean(errors.pullRequestNumber)}
            aria-describedby={errors.pullRequestNumber ? "pull-request-error" : undefined}
            placeholder="42 or https://github.com/owner/repository/pull/42"
            value={values.pullRequestNumber}
            onChange={(event) => update("pullRequestNumber", event.target.value)}
          />
          {errors.pullRequestNumber ? (
            <p id="pull-request-error" className="field-help">
              {errors.pullRequestNumber}
            </p>
          ) : null}
        </div>
      ) : values.requirementMode === "issue" ? (
        <div className="field">
          <label htmlFor="issue-number">Issue number or URL</label>
          <input
            id="issue-number"
            name="issueNumber"
            type="text"
            aria-invalid={Boolean(errors.issueNumber)}
            aria-describedby={errors.issueNumber ? "issue-error" : undefined}
            placeholder="42 or https://github.com/owner/repository/issues/42"
            value={values.issueNumber}
            onChange={(event) => update("issueNumber", event.target.value)}
          />
          {errors.issueNumber ? (
            <p id="issue-error" className="field-help">
              {errors.issueNumber}
            </p>
          ) : null}
        </div>
      ) : (
        <div className="field">
          <label htmlFor="requirement-text">Requirement</label>
          <textarea
            id="requirement-text"
            name="requirementText"
            rows={7}
            aria-invalid={Boolean(errors.requirementText)}
            aria-describedby={errors.requirementText ? "requirement-error" : undefined}
            placeholder="Describe the outcome, constraints, and acceptance criteria."
            value={values.requirementText}
            onChange={(event) => update("requirementText", event.target.value)}
          />
          {errors.requirementText ? (
            <p id="requirement-error" className="field-help">
              {errors.requirementText}
            </p>
          ) : null}
        </div>
      )}

      <div className="field">
        <label htmlFor="primary-model">
          {values.workflowType === "pull_request_review"
            ? "Adjudicator model"
            : "Primary model"}
        </label>
        <select
          id="primary-model"
          name="primaryModel"
          value={values.primaryModel}
          onChange={(event) => update("primaryModel", event.target.value as PrimaryModel)}
          aria-invalid={Boolean(errors.primaryModel)}
          aria-describedby="primary-model-help"
        >
          {modelPairs.length === 0 ? (
            <option value="" disabled>
              No model pairs configured
            </option>
          ) : null}
          {modelPairs.map((pair) => (
            <option
              disabled={!pairIsAvailable(pair.primary_model)}
              key={pair.primary_model}
              value={pair.primary_model}
            >
              {formatModelName(pair.primary_model)}
              {!pairIsAvailable(pair.primary_model) ? " (unavailable)" : ""}
            </option>
          ))}
        </select>
        <p id="primary-model-help" className="field-help">
          {noModelsAvailable
            ? "No required models are currently available. Try again after Copilot is ready."
            : values.workflowType === "pull_request_review"
              ? `${adjudicator} and ${reviewer} review independently. ${adjudicator} consolidates their findings.`
              : `Independent review will use ${reviewer}.`}
        </p>
      </div>

      <div className="form-actions">
        <button className="button" type="submit" disabled={isSubmitting || noModelsAvailable}>
          {isSubmitting
            ? "Creating run..."
            : values.workflowType === "pull_request_review"
              ? "Create review"
              : "Create run"}
        </button>
      </div>
    </form>
  );
}

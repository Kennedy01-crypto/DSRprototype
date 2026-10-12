import { Check, Circle } from "lucide-react";

const stages = [
  {
    state: "Submitted",
    dpo: "Submitted",
    patient: "Request received",
  },
  {
    state: "Accepted / Under Assessment",
    dpo: "Assessment",
    patient: "Reviewing your request",
  },
  {
    state: "Departmental Search",
    dpo: "Department search",
    patient: "Checking relevant records",
  },
  {
    state: "Privacy Review",
    dpo: "Privacy review",
    patient: "Reviewing privacy requirements",
  },
  {
    state: "Legal / Management Review",
    dpo: "Legal / management",
    patient: "Completing required review",
  },
  {
    state: "Response Preparation",
    dpo: "Preparing response",
    patient: "Preparing your response",
  },
];

const statusGuidance: Record<string, { heading: string; detail: string }> = {
  Submitted: {
    heading: "Request received",
    detail: "Your request has been received and is awaiting assessment.",
  },
  "Accepted / Under Assessment": {
    heading: "Your request is being assessed",
    detail: "The team is reviewing your request and deciding what work is needed.",
  },
  "Departmental Search": {
    heading: "Relevant records are being checked",
    detail: "The team is checking the relevant department for information related to your request.",
  },
  "Privacy Review": {
    heading: "Privacy review in progress",
    detail: "The team is checking the information and how it can be provided.",
  },
  "Legal / Management Review": {
    heading: "Additional review in progress",
    detail: "The request is receiving an additional legal or management review.",
  },
  "Response Preparation": {
    heading: "Your response is being prepared",
    detail: "The team is preparing the response to your request.",
  },
  "Waiting for Subject Information": {
    heading: "Action needed: please reply",
    detail: "The team needs information from you before work can continue. Read the Messages section below to see the question and reply.",
  },
  Escalated: {
    heading: "Request referred for additional review",
    detail: "The request has been referred for additional review. The team will continue processing it after that review.",
  },
  Closed: {
    heading: "Request completed",
    detail: "This request is closed. Check Published responses for the outcome.",
  },
  Rejected: {
    heading: "Request not approved",
    detail: "This request has been closed without approval. Check the messages or contact the DPO if you need clarification.",
  },
  Withdrawn: {
    heading: "Request withdrawn",
    detail: "This request has been withdrawn and will not continue through processing.",
  },
};

export function RequestProgress({
  state,
  audience,
}: {
  state: string;
  audience: "dpo" | "patient";
}) {
  const guidance = statusGuidance[state] ?? {
    heading: state,
    detail: "The request status has been updated.",
  };
  const specialState =
    state === "Waiting for Subject Information" ||
    state === "Escalated" ||
    state === "Closed" ||
    state === "Rejected" ||
    state === "Withdrawn";
  const currentStage = stages.findIndex((stage) => stage.state === state);
  const detail =
    audience === "dpo" && state === "Waiting for Subject Information"
      ? "The case is paused until requested information is received. Review the patient messages and follow up if needed."
      : audience === "dpo" && state === "Closed"
        ? "The case is closed. Review the recorded outcome and published response."
        : guidance.detail;

  return (
    <section aria-label="Request progress" className="space-y-4">
      <div className="rounded-lg border bg-muted/35 p-4" role="status">
        <h2 className="text-sm font-semibold">{guidance.heading}</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {audience === "patient" ? detail : `${detail} Current status: ${state}.`}
        </p>
      </div>
      {!specialState && currentStage >= 0 && (
        <ol
          aria-label="Processing stages"
          className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6"
        >
          {stages.map((stage, index) => {
            const completed = index < currentStage;
            const current = index === currentStage;
            return (
              <li
                key={stage.state}
                aria-current={current ? "step" : undefined}
                className={`rounded-lg border p-3 ${
                  current
                    ? "border-primary bg-primary/5"
                    : completed
                      ? "border-emerald-200 bg-emerald-50/60"
                      : "bg-background"
                }`}
              >
                <div className="flex items-start gap-2">
                  {completed ? (
                    <Check
                      aria-hidden="true"
                      className="mt-0.5 size-4 shrink-0 text-emerald-700"
                    />
                  ) : (
                    <Circle
                      aria-hidden="true"
                      className={`mt-0.5 size-4 shrink-0 ${current ? "text-primary" : "text-muted-foreground"}`}
                    />
                  )}
                  <span
                    className={`text-xs leading-5 ${
                      current ? "font-semibold text-foreground" : "text-muted-foreground"
                    }`}
                  >
                    {audience === "patient" ? stage.patient : stage.dpo}
                    {current ? " · Current" : completed ? " · Done" : ""}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

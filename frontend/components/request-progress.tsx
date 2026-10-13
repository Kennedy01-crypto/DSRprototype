import { Check, Circle } from "lucide-react";

const stages = [
  { state: "Submitted", patient: "Request received", dpo: "Submitted" },
  { state: "In Progress", patient: "In progress", dpo: "In progress" },
  { state: "Completed", patient: "Request completed", dpo: "Completed" },
];

const subStatusGuidance: Record<string, string> = {
  "Departmental Search": "Relevant departments are searching their records.",
  "Privacy Review": "The DPO is reviewing the request and search results.",
  "Legal / Management Review": "The request is receiving legal or management review.",
  "Waiting for Subject Information": "The case is waiting for a response from the requester.",
  "Response Preparation": "The response is being prepared.",
  Fulfilled: "The request was fulfilled and the response was communicated.",
  "Partially fulfilled": "The request was partially fulfilled and the outcome was communicated.",
  Rejected: "The request was refused and the decision was communicated.",
  Withdrawn: "The requester withdrew the request.",
  Duplicate: "The request was closed as a duplicate.",
};

export function RequestProgress({
  state,
  subStatus,
  outcome,
  requiresPatientResponse = false,
  audience,
}: {
  state: string;
  subStatus?: string;
  outcome?: string;
  requiresPatientResponse?: boolean;
  audience: "dpo" | "patient";
}) {
  const patientActionNeeded =
    audience === "patient" && requiresPatientResponse;
  const displayOutcome = outcome || subStatus || "";
  const patientDetail = patientActionNeeded
    ? "The team needs more information from you. Open Messages to read the question and reply."
    : state === "Submitted"
      ? "Your request has been received and is awaiting assessment."
      : state === "Completed"
        ? `The organization has finished processing and communicated the outcome${displayOutcome ? `: ${displayOutcome}` : ""}.`
        : "Your request is being processed.";
  const dpoDetail =
    state === "In Progress" && subStatus
      ? `${subStatusGuidance[subStatus] ?? "The request is being processed."} Current stage: ${subStatus}.`
      : state === "Completed" && displayOutcome
        ? `${subStatusGuidance[displayOutcome] ?? "The request has been completed."} Outcome: ${displayOutcome}.`
        : state === "Submitted"
          ? "The request has been received and is awaiting assessment."
          : "The request is being processed.";
  const detail = audience === "patient" ? patientDetail : dpoDetail;
  const currentStage = stages.findIndex((stage) => stage.state === state);

  return (
    <section aria-label="Request progress" className="space-y-4">
      <div className="rounded-lg border bg-muted/35 p-4" role="status">
        <h2 className="text-sm font-semibold">
          {patientActionNeeded ? "Action needed: please reply" : state}
        </h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">{detail}</p>
      </div>
      {currentStage >= 0 && (
        <ol
          aria-label="Request status"
          className="grid gap-3 sm:grid-cols-3"
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

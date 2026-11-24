import { redirect } from "next/navigation";

import { IndependentReviewQueue } from "@/components/independent-review-queue";
import { getDemoSession } from "@/lib/session";

export default async function ReviewerPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "independent_reviewer") redirect("/login");
  return <IndependentReviewQueue principalId={session.principalId} />;
}

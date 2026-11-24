import { notFound, redirect } from "next/navigation";

import { DpoRequestDetail } from "@/components/dpo-request-detail";
import { getDemoSession } from "@/lib/session";

type ReviewerRequestPageProps = {
  params: Promise<{ id: string }>;
};

export default async function ReviewerRequestPage({ params }: ReviewerRequestPageProps) {
  const session = await getDemoSession();
  if (!session || session.role !== "independent_reviewer") redirect("/login");
  const { id } = await params;
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) notFound();
  return (
    <DpoRequestDetail
      key={id}
      id={Number(id)}
      principalId={session.principalId}
      role="independent_reviewer"
    />
  );
}

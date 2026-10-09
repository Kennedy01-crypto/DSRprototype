import { notFound, redirect } from "next/navigation";

import { DpoRequestDetail } from "@/components/dpo-request-detail";
import { getDemoSession } from "@/lib/session";

type DpoRequestPageProps = {
  params: Promise<{ id: string }>;
};

export default async function DpoRequestPage({ params }: DpoRequestPageProps) {
  const session = await getDemoSession();
  if (!session || session.role !== "dpo") redirect("/login");

  const { id } = await params;
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) notFound();

  return (
    <DpoRequestDetail
      key={id}
      id={Number(id)}
      principalId={session.principalId}
    />
  );
}

import { notFound, redirect } from "next/navigation";

import { PatientRequestDetail } from "@/components/patient-request-detail";
import { getDemoSession } from "@/lib/session";

type PatientCommunicationsPageProps = {
  params: Promise<{ id: string }>;
};

export default async function PatientCommunicationsPage({
  params,
}: PatientCommunicationsPageProps) {
  const session = await getDemoSession();
  if (!session || session.role !== "patient") redirect("/login");

  const { id } = await params;
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) notFound();

  return (
    <PatientRequestDetail
      key={id}
      id={Number(id)}
      principalId={session.principalId}
      communicationsOnly
    />
  );
}

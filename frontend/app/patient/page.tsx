import { redirect } from "next/navigation";

import { PatientDashboard } from "@/components/patient-dashboard";
import { getDemoSession } from "@/lib/session";

export default async function PatientPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "patient") redirect("/login");
  return <PatientDashboard principalId={session.principalId} />;
}

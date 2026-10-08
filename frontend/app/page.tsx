import { redirect } from "next/navigation";

import { getDemoSession } from "@/lib/session";

export default async function HomePage() {
  const session = await getDemoSession();
  if (!session) redirect("/login");
  redirect(session.role === "dpo" ? "/dpo" : "/patient");
}

import { redirect } from "next/navigation";

import { LoginForm } from "@/components/login-form";
import { getDemoSession } from "@/lib/session";

export default async function LoginPage() {
  const session = await getDemoSession();
  if (session) redirect(session.role === "dpo" ? "/dpo" : "/patient");
  return <LoginForm />;
}

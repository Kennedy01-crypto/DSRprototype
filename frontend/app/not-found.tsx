import Link from "next/link";
import { ArrowLeft, CircleHelp } from "lucide-react";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-lg rounded-2xl border bg-white p-8 text-center shadow-sm sm:p-10">
        <div className="mx-auto flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <CircleHelp className="size-7" />
        </div>
        <p className="mt-6 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          PAGE NOT FOUND
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">
          We couldn’t find that page
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          The link may be outdated, or the page may have moved. Check the address
          or return to the start of the DSR workspace.
        </p>
        <div className="mt-7 flex flex-wrap justify-center gap-3">
          <Link
            href="/"
            className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <ArrowLeft className="size-4" />
            Back to home
          </Link>
          <Link
            href="/login"
            className="inline-flex h-9 items-center justify-center rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            Sign in
          </Link>
        </div>
      </div>
    </main>
  );
}

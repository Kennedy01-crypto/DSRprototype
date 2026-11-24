"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { DSRAssignedReview } from "@/lib/types";

export function IndependentReviewQueue({ principalId }: { principalId: string }) {
  const [reviews, setReviews] = useState<DSRAssignedReview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadReviews() {
      try {
        const response = await fetch("/api/assigned-reviews", { cache: "no-store" });
        const body = (await response.json()) as DSRAssignedReview[] | { detail?: string };
        if (!response.ok || !Array.isArray(body)) {
          throw new Error(!Array.isArray(body) ? body.detail : "Could not load assigned reviews.");
        }
        if (!cancelled) setReviews(body);
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Could not load assigned reviews.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadReviews();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AppShell role="independent_reviewer" principalId={principalId}>
      <section className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          INDEPENDENT REVIEW
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Assigned reviews</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Review the exact response version and case evidence assigned to you.
          Return stale material for changes instead of approving it.
        </p>
      </section>
      {error && <p role="alert" className="mb-4 text-sm text-destructive">{error}</p>}
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading assigned reviews…</p>
      ) : reviews.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No independent reviews are assigned to this identity.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {reviews.map((review) => (
            <Link key={review.request_id} href={`/reviewer/requests/${review.request_id}`}>
              <Card className="h-full transition hover:ring-primary/50">
                <CardHeader>
                  <CardTitle>DSR-{review.request_id} · {review.request_type}</CardTitle>
                  <CardDescription>
                    Version {review.response_version} · {review.proposed_outcome}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p>{review.request_reason}</p>
                  <p className="text-xs text-muted-foreground">
                    Requested {new Date(review.requested_at).toLocaleString()} by {review.requested_by}
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </AppShell>
  );
}

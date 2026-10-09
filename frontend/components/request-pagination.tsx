import { Button } from "@/components/ui/button";

export const REQUESTS_PER_PAGE = 6;

export function RequestPagination({
  page,
  totalItems,
  onPageChange,
}: {
  page: number;
  totalItems: number;
  onPageChange: (page: number) => void;
}) {
  const pageCount = Math.ceil(totalItems / REQUESTS_PER_PAGE);
  if (pageCount <= 1) return null;

  return (
    <nav
      aria-label="Request list pages"
      className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"
    >
      <p className="text-xs text-muted-foreground">
        Page {page} of {pageCount} · {totalItems} requests
      </p>
      <div className="flex flex-wrap items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </Button>
        {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
          <Button
            key={number}
            variant={number === page ? "default" : "outline"}
            size="sm"
            aria-current={number === page ? "page" : undefined}
            aria-label={`Page ${number}`}
            onClick={() => onPageChange(number)}
          >
            {number}
          </Button>
        ))}
        <Button
          variant="outline"
          size="sm"
          disabled={page === pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}

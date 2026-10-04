import "./Pagination.css";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onPage: (newOffset: number) => void;
}

/** Pagination controls with accessible labels and explicit page context. */
export function Pagination({ total, limit, offset, onPage }: PaginationProps) {
  if (total <= limit) return null;

  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  const startItem = offset + 1;
  const endItem = Math.min(offset + limit, total);

  return (
    <nav className="pagination" aria-label="Pagination">
      <span className="pagination__context">
        {startItem}–{endItem} of {total}
      </span>
      <div className="pagination__controls">
        <button
          type="button"
          className="pagination__btn"
          disabled={!hasPrev}
          onClick={() => onPage(Math.max(0, offset - limit))}
          aria-label="Previous page"
        >
          ← Prev
        </button>
        <span className="pagination__page" aria-current="page">
          {currentPage} / {totalPages}
        </span>
        <button
          type="button"
          className="pagination__btn"
          disabled={!hasNext}
          onClick={() => onPage(offset + limit)}
          aria-label="Next page"
        >
          Next →
        </button>
      </div>
    </nav>
  );
}

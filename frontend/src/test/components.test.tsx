/**
 * Component rendering tests: Badge, StateViews, Pagination, MetricCard.
 * Tests are focused on rendering correctness and accessibility semantics.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  Badge,
  PriorityBadge,
  ConfidenceBadge,
  StatusBadge,
  CategoryBadge,
} from "../components/Badge";
import {
  LoadingState,
  ErrorState,
  EmptyState,
} from "../components/StateViews";
import { MetricCard } from "../components/MetricCard";
import { Pagination } from "../components/Pagination";

// ── Badge ─────────────────────────────────────────────────────────────────────

describe("Badge", () => {
  it("renders the label text", () => {
    render(<Badge label="TEST" />);
    expect(screen.getByText("TEST")).toBeInTheDocument();
  });

  it("applies the tone class", () => {
    const { container } = render(<Badge label="CRITICAL" tone="danger" />);
    expect(container.firstChild).toHaveClass("badge--danger");
  });
});

describe("PriorityBadge", () => {
  it("renders CRITICAL with danger tone", () => {
    const { container } = render(<PriorityBadge priority="CRITICAL" />);
    expect(container.querySelector(".badge--danger")).not.toBeNull();
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
  });

  it("renders LOW with muted tone", () => {
    const { container } = render(<PriorityBadge priority="LOW" />);
    expect(container.querySelector(".badge--muted")).not.toBeNull();
  });
});

describe("ConfidenceBadge", () => {
  it("shows — when confidence is null", () => {
    render(<ConfidenceBadge confidence={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows HIGH with success tone", () => {
    const { container } = render(<ConfidenceBadge confidence="HIGH" />);
    expect(container.querySelector(".badge--success")).not.toBeNull();
  });
});

describe("StatusBadge", () => {
  it("renders IN REVIEW (space-separated)", () => {
    render(<StatusBadge status="IN_REVIEW" />);
    expect(screen.getByText("IN REVIEW")).toBeInTheDocument();
  });
});

describe("CategoryBadge", () => {
  it("renders human-readable label", () => {
    render(<CategoryBadge category="EXECUTION_GAP" />);
    expect(screen.getByText("Execution Gap")).toBeInTheDocument();
  });

  it("falls back to raw value for unknown category", () => {
    render(<CategoryBadge category="UNKNOWN_CAT" />);
    expect(screen.getByText("UNKNOWN_CAT")).toBeInTheDocument();
  });
});

// ── StateViews ────────────────────────────────────────────────────────────────

describe("LoadingState", () => {
  it("has role=status for screen readers", () => {
    render(<LoadingState />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows custom label", () => {
    render(<LoadingState label="Fetching data…" />);
    expect(screen.getByText("Fetching data…")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("has role=alert", () => {
    render(<ErrorState message="Something went wrong." />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("shows the message", () => {
    render(<ErrorState message="Backend is down." />);
    expect(screen.getByText("Backend is down.")).toBeInTheDocument();
  });

  it("shows retry button when onRetry provided", () => {
    const retry = vi.fn();
    render(<ErrorState message="Error" onRetry={retry} />);
    const btn = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(btn);
    expect(retry).toHaveBeenCalledOnce();
  });

  it("shows backend-not-reachable title for connection errors", () => {
    render(<ErrorState message="timeout" isConnectionError />);
    expect(screen.getByText(/backend not reachable/i)).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("shows title and body", () => {
    render(<EmptyState title="No data" body="Run an assessment first." />);
    expect(screen.getByText("No data")).toBeInTheDocument();
    expect(screen.getByText("Run an assessment first.")).toBeInTheDocument();
  });
});

// ── MetricCard ────────────────────────────────────────────────────────────────

describe("MetricCard", () => {
  it("shows label and value", () => {
    render(<MetricCard label="Total findings" value={245} />);
    expect(screen.getByText("Total findings")).toBeInTheDocument();
    expect(screen.getByText("245")).toBeInTheDocument();
  });

  it("shows sub text when provided", () => {
    render(<MetricCard label="Open" value={10} sub="awaiting review" />);
    expect(screen.getByText("awaiting review")).toBeInTheDocument();
  });

  it("applies tone class", () => {
    const { container } = render(<MetricCard label="Critical" value={5} tone="danger" />);
    expect(container.firstChild).toHaveClass("metric-card--danger");
  });
});

// ── Pagination ────────────────────────────────────────────────────────────────

describe("Pagination", () => {
  it("returns null when total <= limit", () => {
    const { container } = render(
      <Pagination total={10} limit={25} offset={0} onPage={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows page context", () => {
    render(<Pagination total={100} limit={25} offset={0} onPage={vi.fn()} />);
    expect(screen.getByText("1–25 of 100")).toBeInTheDocument();
  });

  it("disables Prev on first page", () => {
    render(<Pagination total={100} limit={25} offset={0} onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
  });

  it("calls onPage with correct offset on Next", () => {
    const onPage = vi.fn();
    render(<Pagination total={100} limit={25} offset={0} onPage={onPage} />);
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(onPage).toHaveBeenCalledWith(25);
  });

  it("disables Next on last page", () => {
    render(<Pagination total={50} limit={25} offset={25} onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });
});

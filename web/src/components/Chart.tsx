import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

interface ChartProps {
  /** Epoch-second timestamps (x axis). */
  ts: number[];
  /** Y values aligned with `ts`. */
  values: number[];
  label: string;
  height?: number;
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/**
 * Thin React wrapper over uPlot. Rebuilds the plot when the data or size
 * changes and disposes it on unmount. A ResizeObserver keeps it fluid-width.
 */
export function Chart({ ts, values, label, height = 260 }: ChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  const data = useMemo<uPlot.AlignedData>(() => [ts, values], [ts, values]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const accent = cssVar("--accent", "#6ea8fe");
    const muted = cssVar("--muted", "#9aa0a6");
    const border = cssVar("--border", "#2a2e35");

    const opts: uPlot.Options = {
      width: container.clientWidth || 600,
      height,
      cursor: { y: false },
      legend: { show: true },
      scales: { x: { time: true } },
      axes: [
        { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border } },
        { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border } },
      ],
      series: [
        {},
        {
          label,
          stroke: accent,
          width: 2,
          fill: `${accent}22`,
          points: { show: values.length < 40 },
        },
      ],
    };

    const plot = new uPlot(opts, data, container);
    plotRef.current = plot;

    const ro = new ResizeObserver(() => {
      plot.setSize({ width: container.clientWidth || 600, height });
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      plot.destroy();
      plotRef.current = null;
    };
  }, [data, label, height, values.length]);

  return <div className="chart" ref={containerRef} aria-label={`${label} chart`} role="img" />;
}

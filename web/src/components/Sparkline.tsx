interface SparklineProps {
  points: number[];
  width?: number;
  height?: number;
  ariaLabel?: string;
}

/**
 * Inline SVG sparkline from a float list — a pure, dependency-free polyline
 * normalized to the viewport. Mirrors the old server-side `spark_svg` helper.
 */
export function Sparkline({ points, width = 110, height = 24, ariaLabel }: SparklineProps) {
  if (points.length < 2) {
    return <span className="muted">—</span>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = width / (points.length - 1);
  const coords = points.map((value, i) => {
    const x = i * stepX;
    // Invert Y so larger values sit higher; pad 1px top/bottom.
    const y = height - 1 - ((value - min) / span) * (height - 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const lastPoint = coords[coords.length - 1] ?? "";
  const [cx, cy] = lastPoint.split(",");
  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel ?? "trend sparkline"}
      preserveAspectRatio="none"
    >
      <polyline
        points={coords.join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {cx && cy && <circle cx={cx} cy={cy} r="1.8" fill="currentColor" />}
    </svg>
  );
}

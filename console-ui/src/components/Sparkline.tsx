import { computeBarHeights, sparklineHasActivity } from '../lib/derive';

export interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  className?: string;
  title?: string;
}

export function Sparkline({
  data,
  width = 60,
  height = 16,
  color = 'var(--color-accent, var(--accent, #6366f1))',
  className,
  title,
}: SparklineProps) {
  if (!sparklineHasActivity(data)) return null;

  const barWidth = width / data.length;
  const gap = Math.max(0.5, barWidth * 0.1);
  const heights = computeBarHeights(data, height);

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: 'inline-block', verticalAlign: 'middle' }}
      role="img"
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {data.map((v, i) => {
        const barHeight = heights[i] ?? 0;
        return (
          <rect
            key={i}
            x={i * barWidth + gap / 2}
            y={height - barHeight}
            width={Math.max(0, barWidth - gap)}
            height={barHeight}
            fill={color}
            opacity={v > 0 ? 0.85 : 0.12}
          />
        );
      })}
    </svg>
  );
}
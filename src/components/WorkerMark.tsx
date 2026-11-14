const pixels = { sm: 24, md: 32, lg: 64 } as const;

const tones = {
  light: { square: "var(--color-background-surface)", frame: "var(--color-border-emphasized)", line: "var(--color-text-primary)" },
  dark: { square: "var(--color-text-primary)", frame: undefined, line: "var(--color-background-surface)" },
} as const;

export function WorkerMark({
  size = "sm",
  tone = "light",
  label = "Worker",
}: {
  size?: keyof typeof pixels;
  tone?: keyof typeof tones;
  label?: string;
}) {
  const lineWidth = size === "lg" ? 1.2 : 1.5;
  const colors = tones[tone];
  return (
    <svg width={pixels[size]} height={pixels[size]} viewBox="0 0 32 32" role="img" aria-label={label} fill="none">
      <rect
        x="0.75"
        y="0.75"
        width="30.5"
        height="30.5"
        rx="8"
        fill={colors.square}
        stroke={colors.frame}
        strokeWidth={colors.frame ? 1.5 : undefined}
      />
      <rect x="7.5" y="9" width="17" height="12" rx="2.5" stroke={colors.line} strokeWidth={lineWidth} />
      <path d="M11 13.5l2.2 1.8L11 17.1" stroke={colors.line} strokeWidth={lineWidth} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M15.5 17.2h4.5M12.5 24.5h7" stroke={colors.line} strokeWidth={lineWidth} strokeLinecap="round" />
    </svg>
  );
}

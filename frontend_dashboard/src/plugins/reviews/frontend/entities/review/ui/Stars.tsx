/**
 * Estrellas doradas (token `--color-yellow`) de 1 a 5. Se leen como UN texto
 * para el lector de pantalla ("4 de 5 estrellas"); los glifos son decorativos.
 * Con un promedio (4,6) se pintan las estrellas redondeadas.
 */

const SIZE_CLASS = { sm: "text-[13px]", lg: "text-[18px]" } as const;

export function Stars({
  value,
  size = "sm",
  label,
}: {
  value: number;
  size?: keyof typeof SIZE_CLASS;
  /** Texto accesible; por defecto "N de 5 estrellas". */
  label?: string;
}) {
  const filled = Math.max(0, Math.min(5, Math.round(value)));
  return (
    <span
      role="img"
      aria-label={label ?? `${filled} de 5 estrellas`}
      className={`inline-flex shrink-0 leading-none tracking-[1px] ${SIZE_CLASS[size]}`}
    >
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} aria-hidden="true" className={i <= filled ? "text-yellow" : "text-fg-faint"}>
          ★
        </span>
      ))}
    </span>
  );
}

/**
 * Avatar circular con iniciales y, opcionalmente, indicador de presencia.
 * Los colores `a–f` mapean a los 6 gradientes del prototipo (ver `index.css`).
 */

interface Props {
  initials: string;
  color?: string;
  size?: number;
  presence?: "online" | "away" | "off";
  className?: string;
}

export function Avatar({ initials, color = "purple", size = 34, presence, className }: Props) {
  const style = size !== 34 ? { width: size, height: size, fontSize: size * 0.32 } : undefined;
  return (
    <div className={["avatar", color, className].filter(Boolean).join(" ")} style={style}>
      {initials}
      {presence && presence !== "off" && (
        <span className={"pres " + (presence === "away" ? "away" : "")} />
      )}
      {presence === "off" && <span className="pres off" />}
    </div>
  );
}

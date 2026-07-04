import { css } from "../../css";

/** Ring spinner (hoisted from DraftActivity so screens stop importing activity internals). */
export default function Spinner({
  size = 13,
  color = "var(--info)",
}: {
  size?: number;
  color?: string;
}) {
  return (
    <span
      style={css(
        `display:inline-block;flex:none;width:${size}px;height:${size}px;border-radius:50%;` +
          `border:2px solid var(--line);border-top-color:${color};animation:spin .8s linear infinite`,
      )}
    />
  );
}

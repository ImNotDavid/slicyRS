import lettermark from "../assets/lettermark.svg";
import harry from "../assets/harry.svg";

/** The ICRS brand marks (single colour, taken from the brand kit): they follow the text colour, so light and dark themes both work. */
const Mark = ({ src, w, h, className, label }: { src: string; w: number; h: number; className?: string; label?: string }) => (
  <span className={"mark-img " + (className ?? "")} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}
    style={{ width: w, height: h, WebkitMaskImage: `url("${src}")`, maskImage: `url("${src}")` }} />
);
export const Lettermark = ({ height = 22, className }: { height?: number; className?: string }) => <Mark src={lettermark} w={Math.round(height * 63 / 23)} h={height} className={className} label="ICRS" />;
export const Harry = ({ height = 55, className }: { height?: number; className?: string }) => <Mark src={harry} w={Math.round(height * 47 / 55)} h={height} className={className} />;

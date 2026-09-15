/**
 * Vazirmatn loading for Persian/RTL ranking text.
 *
 * The font ships in `apps/web/public/fonts/` and is declared with @font-face in
 * index.css. This hook waits for the browser to actually load it so the canvas
 * text renderer never rasterizes a Persian string with a fallback font.
 */
import { useEffect, useState } from "react";

export const RANKING_FONT_FAMILY = "Vazirmatn";
export const RANKING_FONT_URL = "/fonts/Vazirmatn-Bold.ttf";

export async function ensureRankingFont(): Promise<boolean> {
  if (typeof document === "undefined" || typeof FontFace === "undefined") {
    return false;
  }
  try {
    await document.fonts.load(`900 64px "${RANKING_FONT_FAMILY}"`, "#1");
    if (document.fonts.check(`900 64px "${RANKING_FONT_FAMILY}"`, "#1")) {
      return true;
    }
    const face = new FontFace(RANKING_FONT_FAMILY, `url(${RANKING_FONT_URL})`, {
      weight: "100 900",
      style: "normal",
      display: "swap",
    });
    await face.load();
    document.fonts.add(face);
    return document.fonts.check(`900 64px "${RANKING_FONT_FAMILY}"`, "#1");
  } catch {
    return false;
  }
}

/** True once Vazirmatn is usable for canvas text. */
export function useRankingFontReady(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let mounted = true;
    void ensureRankingFont().then((ok) => {
      if (mounted) setReady(ok);
    });
    return () => {
      mounted = false;
    };
  }, []);
  return ready;
}

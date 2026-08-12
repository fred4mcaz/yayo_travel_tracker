import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { polyfillCountryFlagEmojis } from "country-flag-emoji-polyfill";
// Vite bundles the font; ?url keeps it out of the JS and off any CDN.
import flagFontUrl from "country-flag-emoji-polyfill/dist/TwemojiCountryFlags.woff2?url";

import { App } from "./App";
import "./styles.css";

// Windows has no flag emoji glyphs (country codes render as "SG" instead of
// a flag), so on browsers that lack them this injects a flags-only web font.
// "Twemoji Country Flags" leads the font stack in styles.css; the font only
// contains the flag code points, so no other text is affected.
polyfillCountryFlagEmojis("Twemoji Country Flags", flagFontUrl);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

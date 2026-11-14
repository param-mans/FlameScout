# FlameScout on iPad

Target: **iPadOS 16.4 or newer** (Safari). Tailwind v4 needs Safari 16.4; the viewer code has no regex lookbehind
and no other syntax newer than Safari 16.

## What the iPad layout does

- `viewport-fit=cover`, `100dvh` and safe-area insets, so it fills the screen (also when added to the home screen).
- Touch first: every button, tab and toggle is at least 44 x 44 px, text is at least 12 px, and there is no hover-only
  behaviour. A double tap does not zoom the page. In the 2D plan, one finger pans and two fingers pinch-zoom.
- At 1180 px wide and below, the viewer's right column has two tabs, **Plan** and **Surroundings** (the Vexcel
  imagery). The inspector opens as a bottom sheet when you tap a room, door or window, and × closes it. The MDT never
  shows more than three zones. In portrait, the side zones stack below the viewer and nothing scrolls sideways.
- Performance: on touch devices the glow effect is off by default. The 3D pixel ratio is capped at 2. If the 3D view
  runs below 30 fps for about 2 s, glow, point markers and heavy transparency are turned off, and the HUD shows
  "REDUCED EFFECTS".
- The incident banner is one line (INCIDENT, version, number of items, version buttons) at most 48 px tall. Tap
  it to open the details. At 1180 px and below it starts closed so it does not cover the 3D view; the choice is kept
  when a new version arrives.
- With `?debug=1`, the scenario bar is collapsed into a **DEBUG** toggle so it does not cover the plan.

## Testing with WebKit (Safari's engine) on a Mac

Playwright drives WebKit with touch (taps, not clicks). It is not a project dependency, so install it without saving:

```sh
cd apps/web
npm i --no-save playwright
npx playwright install webkit
npm run dev                                   # in another terminal
node scripts/ipad-webkit.mjs                  # add --shots ../../docs/capturas/ui to save screenshots
```

The script opens Fairdale (`?sample=209217448&debug=1`) at 1180x820, 1366x1024, 1080x810 and 1024x768 in landscape,
and at 820x1180 and 810x1080 in portrait. In landscape it taps through these steps:

1. Choose a floor.
2. Open LAYERS.
3. Tap a room.
4. Tap "Show on plan".
5. Switch to the Surroundings tab and change the Vexcel direction.
6. Open DEBUG and load scenario S4.

It fails, with exit code 1, on any of these:
- a visible interactive element smaller than 44 x 44 px, in the page or in the viewer iframe, measured with LAYERS
  open, with the inspector open and at the end;
- horizontal page scroll;
- a console error.

Playwright's WebKit is close to Safari but not identical. Test on a real iPad before relying on it.

## On a real iPad over the local network

1. The Mac and the iPad must be on the same Wi-Fi.
2. On the Mac, run `npm run dev -- --host` in `apps/web`. Vite prints a `Network:` address such as `http://192.168.1.20:5173/`.
3. Open that address in Safari on the iPad. If it does not load, allow incoming connections for Node in the
   macOS firewall.
4. The house data routes (`/api/house-viewer/...`) run inside the dev server and read the keys from `.env` on the
   Mac. The keys never reach the iPad.
5. For console errors, connect the iPad by cable, enable Settings › Safari › Advanced › Web Inspector, and use
   Safari on the Mac › Develop › (your iPad).

## On the deployed URL

Open the deployed address (behind Caddy, over HTTPS) in Safari. The `house-data` service serves the same routes as
the dev server.

## Add to home screen

In Safari, tap Share › **Add to Home Screen**. The page declares `apple-mobile-web-app-capable`, so it opens full screen
without Safari's bars, and the layout keeps clear of the status bar and home indicator through the safe-area insets.

## Known limits

- Below 1040 px in landscape, the size-up column is narrow and long values wrap onto several lines.
- The fps check runs once, about 2 s after load. Effects are not turned back on if the device later speeds up.

# Visual conventions

The brief, the webpage, and the generated PDF all render the same data through different rendering paths (Python SVG for the brief and PDF, server-rendered React/SVG for the webpage). To keep the user experience consistent and the visuals readable, every chart in this project follows the conventions below. New charts should match.

## Color palette

| Role | Color | Hex / RGB |
|---|---|---|
| Confirmed cases, primary brand orange | orange-clay | `#c6613f`, `rgb(198 97 63)` |
| Inferred underlying, secondary orange | orange-band | `rgb(217 119 87)` |
| Suspected cases / corridor target / lake | blue-slate | `rgb(106 155 204)` (fills), `rgb(53 91 128)` (strokes) |
| Deaths | green-fern | `rgb(77 112 82)` stroke, `rgb(130 165 120)` fill |
| Mahagi/Arua border-crossing watch | gray-purple | `rgb(150 145 165)` fill, `rgb(100 95 115)` stroke |
| Body text | brown-ink | `rgb(26 23 19)` |
| Muted text | gray-tan | `rgb(110 104 95)` |
| Surface | cream | `rgb(255 252 245)`, lighter `rgb(247 243 233)` |

## Legend backgrounds

Every map legend sits on a rounded cream panel so labels read cleanly over country outlines, lakes, and corridor lines:

```jsx
<rect
  x={0} y={0}
  width={legendWidth}
  height={legendHeight}
  fill="rgb(255 252 245 / 0.96)"   // semi-opaque cream
  stroke="rgb(26 23 19 / 0.10)"
  strokeWidth={1}
  rx={6}
/>
```

Applied to both `GeographicMap.tsx` and `CorridorWatchlistMap.tsx`. New maps that overlay legend chips onto a base SVG MUST use this pattern.

## Label discipline

Reading rule: a label on a chart should answer one question. Avoid stacking labels that answer the same question in two formats.

| Pattern | Where it applies | Example |
|---|---|---|
| Inline percentage as chip with white pill background | Anchor metrics that resolve later (calibration corridors, ascertainment lower/upper) | CorridorWatchlistMap calibration chips at the four pre-committed corridors |
| Bare endpoint number, no chip | Time-series endpoints (trajectory chart deaths "144", suspected "653", confirmed "51") | TrajectoryChart endpoint labels |
| No inline label, see companion table | When the same number is already in an adjacent table or bar chart | CorridorWatchlistMap descriptive corridors (16 of 20); the bar chart above lists them all numerically |

When two labels would otherwise share a vertical position, separate them by **at least 48 px horizontally** (e.g. callout offset = as-of-x + 36 in TrajectoryChart).

## JSX whitespace around `<em>` and `<code>` tags

When inline italic or monospace falls inside flowing text, ALWAYS bracket with explicit `{' '}` tokens. JSX collapses whitespace across line breaks and can eat the space:

```jsx
// WRONG
<p>
  ...is roughly <em>2.4 to 2.9 times</em> the public-confirmed count, ...
</p>

// CORRECT
<p>
  ...is roughly{' '}
  <em>2.4 to 2.9 times</em>{' '}
  the public-confirmed count, ...
</p>
```

Same rule for `<code>` and `<strong>` when followed by lowercase text.

## Font sizes

| Use | Size |
|---|---|
| Chart endpoint labels | 11-12 px, bold |
| Numeric chip labels (in pill) | 10 px, bold, monospace |
| Caption text (legends, axis labels) | 10 px, regular |
| Section header (h2) | 20 px (Tailwind `text-xl`) bold |
| Body | 16 px (Tailwind `text-base`), line-height 1.6 |

## Chart construction checklist (apply to new charts)

- [ ] Right-padding wide enough that endpoint labels never run into the viewbox edge. Add 60 px minimum for numeric labels at fontSize 13.
- [ ] Endpoint labels offset ≥ 8 px from the data point marker.
- [ ] Multi-line label stacks have at least 14 px vertical spacing per row.
- [ ] When two label families share the same x or y band (e.g. deaths label LEFT of marker, band label RIGHT of marker), separate them by ≥ 30 px horizontal callout offset.
- [ ] Legend has a rounded background panel; never sits directly on the chart plot area.
- [ ] All numeric values shown on the chart must trace to a row in `NUMBERS_AUDIT.md`.
- [ ] How-to-read paragraph below the chart explains what is and is NOT being measured.

## When updating a number

1. Update the row in `NUMBERS_AUDIT.md`.
2. Re-run `python refresh_pipeline.py`.
3. Re-run `python sync_to_website.py` to mirror updated zones, snapshot, and brief PDF into the website.
4. Re-run `python make_brief.py` to regenerate the standalone HTML/PDF.
5. Check the rendered chart visually for crowding and any new label collisions.
6. Commit, with a one-line summary linking back to the row(s) in `NUMBERS_AUDIT.md` that changed.

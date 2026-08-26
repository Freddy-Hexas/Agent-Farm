# Agent Farm website brand specification

## Scope

This file covers the standalone Agent Farm marketing-site prototype in `website/`. It does not
change the native WinUI product or the existing Python web console.

## Brand assets

- Primary mark: `assets/agent-farm-brand/agent-farm-512x512.png`
- Source artwork in the repository: `../branding/agent-farm-logo/agent-farm-source.svg`
- Product screenshots are intentionally excluded from this brand-concept page. Recognition is carried
  by the official logo, the Agent Farm routing thesis, and a clearly labeled abstract simulation.

The logo must be referenced as an image. Do not redraw it in CSS or substitute a text monogram.

## Color system

- Brand blue: `#017BC6` from the official logo SVG
- Ground: `#08090A`
- Deep ground: `#020304`
- Surface 1: `#101216`
- Surface 2: `#171A20`
- Surface 3: `#20242C`
- Primary text: `#F4F7FA`
- Secondary text: `#A6AFBD`
- Muted text: `#66707E`
- Hairline: `rgba(255,255,255,0.08)`

Blue variants are derived from the brand hue in OKLCH. Purple, pink, and unrelated decorative hues
are not part of this website system. Positive and negative colors are reserved for semantic state.

## Typography

- Display and body: Geist
- Operational labels and data: Geist Mono
- Display tracking: tight, from `-0.062em` to `-0.02em`, with display line height no tighter than `0.96`
- Body copy: 16–21px with generous line height
- Operational surfaces: 8–12px mono labels, grouped through alignment and hairlines

## Shape and elevation

- Operational panes: 0–4px radius
- Buttons: 8px radius
- Full product surfaces: 12px radius maximum
- Shadows are reserved for the two product-stage surfaces. All internal grouping uses hairlines.

## Motion

- One ambient hero mesh and one task-routing choreography per scene
- The routing demo uses restrained perspective, pointer parallax, three explicit Worker branches, and one evidence merge
- The routing choreography is autonomous: active routes illuminate from intent to decision, hold briefly, then loop without visible playback controls
- Hover feedback: 160–220ms using `cubic-bezier(0.16, 1, 0.3, 1)`
- Layout reveals: 700–900ms using `cubic-bezier(0.22, 1, 0.36, 1)`
- No spring, bounce, audio, or repeated spectacle
- Reduced-motion mode preserves all content and removes continuous motion
- At narrow widths the routing demo becomes a flat vertical branch diagram so labels remain legible

## Content constraints

- Do not fabricate customer logos, testimonials, usage metrics, benchmark results, or savings claims.
- Capability statements must be grounded in the repository README and product documentation.
- Do not add download or installation calls to action until the user requests them.
- The routing sculpture explains architecture and does not estimate savings.
- The interactive flow is labeled as a local simulation and makes no model calls.

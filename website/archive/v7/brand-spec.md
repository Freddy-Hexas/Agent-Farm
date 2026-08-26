# Agent Farm website brand specification

## Scope

This file covers the standalone Agent Farm product website in `website/`. It does not
change the native WinUI product or the existing Python web console.

## Brand assets

- Primary mark: `assets/agent-farm-brand/agent-farm-512x512.png`
- Source artwork in the repository: `../branding/agent-farm-logo/agent-farm-source.svg`
- Product screenshots are intentionally excluded at the user's direction. Recognition is carried by
  the official logo, factual product architecture, and a clearly labeled reference visualization.

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

## Information architecture

- Product: token-cost problem, product identity, and core operating properties
- Workflow: plan and route, isolate and execute, stream and collect, review and decide
- Architecture: one reference routing visualization embedded inside the product narrative
- Controls: cost boundaries, execution scope, review evidence, and reversible changes
- Use cases: repository changes, research synthesis, and mixed hosted/local model stacks
- Footer: internal navigation plus real version, platform, and license metadata

Standalone full-screen manifesto slides and public prototype controls are not part of the production
site. The routing visualization supports the story; it is not the site's only source of product meaning.

## Motion

- One ambient hero mesh and one task-routing choreography per scene
- The routing visualization uses restrained perspective, pointer parallax, three explicit Worker branches, and one evidence merge
- The routing choreography is autonomous: active routes illuminate from intent to decision, hold briefly, then loop without visible playback controls
- Motion layers are restrained and synchronized: directional dash flow, arrow bloom, active-node scan, slow grid drift, and phase-copy transitions
- Hover feedback: 160–220ms using `cubic-bezier(0.16, 1, 0.3, 1)`
- Layout reveals: 700–900ms using `cubic-bezier(0.22, 1, 0.36, 1)`
- No spring, bounce, audio, or repeated spectacle
- Reduced-motion mode preserves all content and removes continuous motion
- At narrow widths the routing visualization becomes a flat vertical branch diagram so labels remain legible

## Content constraints

- Do not fabricate customer logos, testimonials, usage metrics, benchmark results, or savings claims.
- Normalized routing scenarios may be used only when they are visibly labeled illustrative and not a
  measured benchmark, define the baseline, and disclose the major factors that can change the result.
  Illustrative indices must never be presented as observed product performance and must be replaced by
  reproducible measurements before they are used as public performance claims.
- Capability statements must be grounded in the repository README and product documentation.
- Do not add download or installation calls to action until the user requests them.
- The architecture visualization explains system flow and does not estimate savings. The separate
  economics chart uses a disclosed baseline-100 scenario to explain the routing hypothesis.
- Version, platform, license, capability, and safety statements must match the current repository documentation.

# Agent Farm logo assets

These assets are generated from the vector shapes in the repository-level `icon.pptx` design without changing its composition. The PowerPoint artwork is preserved as `agent-farm-source.svg` and rendered to a 4096 x 4096 source canvas before producing platform assets.

## Deliverables

- `agent-farm-master-transparent.png`: 1024 x 1024 transparent master
- `agent-farm-source.svg`: original vector artwork extracted from `icon.pptx`
- `agent-farm-source-4096.png`: high-resolution square raster source rendered from that vector artwork
- `agent-farm.ico`: multi-resolution Windows icon containing 16, 20, 24, 32, 40, 48, 64, 96, 128, and 256 px frames
- `png/`: transparent PNG exports from 16 x 16 through 1024 x 1024
- `logo-preview.png`: light and dark background legibility preview

The generated WinUI/MSIX variants are written to `AgentFarm.Desktop/Assets`. The 16-48 px variants use small-size stroke expansion and a tighter antialiasing ramp so the title-bar icon remains sharp on high-DPI displays.

Regenerate the complete asset family from the repository root:

```powershell
python packaging\generate_brand_assets.py
```

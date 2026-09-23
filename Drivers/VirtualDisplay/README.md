Virtual display driver payload placeholder.

This directory is intentionally packaged with the agent so the service-side
display substrate manager can discover and provision a Windows-supported
virtual or indirect display driver when one is supplied.

What belongs here:
- A real Windows virtual display or IDD driver package
- The `.inf`, `.cat`, and `.sys` files for that package
- Optional `devcon.exe` if the package requires device instantiation or repair
- A concrete `driver_manifest.json` derived from `driver_manifest.json.example`

Important:
- The current repository does not include a production driver payload.
- Without a real persistent display substrate, capture continuity across
  `mstsc` minimize, RDP disconnect, lock screen, or session switching cannot be
  guaranteed on headless targets.
- Freeze detection and helper migration improve diagnostics, but they do not
  replace an actual display substrate.

Recommended layout:
- `Drivers/VirtualDisplay/driver_manifest.json`
- `Drivers/VirtualDisplay/<vendor>.inf`
- `Drivers/VirtualDisplay/<vendor>.cat`
- `Drivers/VirtualDisplay/<vendor>.sys`
- `Drivers/VirtualDisplay/devcon.exe` (optional)

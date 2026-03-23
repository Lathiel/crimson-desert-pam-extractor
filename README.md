# Crimson Desert PAM Extractor

Extracts mesh geometry from Crimson Desert `.pam` and `.pamlod` game files and exports to **FBX 7.4** (Blender-compatible) and **OBJ**.

## Requirements

**Python 3.8 or newer** — https://www.python.org/downloads/

No additional packages required. All dependencies (`struct`, `pathlib`, `argparse`, etc.) are part of the Python standard library.

## Usage

### GUI (Windows)
Double-click `extract.bat` — it will open file picker dialogs and handle everything automatically:
1. Pick one or more `.pam` files
2. It auto-discovers matching `.pamlod` LOD files and `.dds` textures in the same folder
3. Output is saved next to each source file


UPDATE: Now with GUI support -> GUI.bat

### Command line
```
python cd_extractor.py <file.pam> [options]
python cd_extractor.py <file.pamlod> [options]

Options:
  -o <dir>       Output directory (default: same as input file)
  --obj          Export as OBJ/MTL instead of FBX
  --info-only    Print mesh info without exporting
  --lod <0-4>    Export specific LOD level from .pamlod (default: LOD0)
  --all-lods     Export all LOD levels from .pamlod
```

## Supported formats

| Format | Description |
|--------|-------------|
| `.pam` | Single mesh or multi-submesh prefab |
| `.pamlod` | LOD (Level of Detail) mesh — up to 5 quality levels |

## Output

- **FBX 7.4 binary** — imports directly into Blender (File → Import → FBX)
- **OBJ + MTL** — universal format, works in any 3D software

Vertex positions are uint16-quantized and dequantized using the per-file bounding box.
UVs are stored as float16 at vertex offset +8/+10.

## Notes

- `.bat` and `.ps1` files may be flagged by antivirus — this is a false positive common with WinForms-based file dialogs. You can review the full source here.
- DDS textures are copied to the output folder automatically if found in the same directory as the PAM.

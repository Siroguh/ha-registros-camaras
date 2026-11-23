from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
source = root / "custom_components" / "registros_camaras"
target = root / "dist" / "registros_camaras.zip"
target.parent.mkdir(exist_ok=True)
with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
    archive.write(root / "LICENSE", "custom_components/registros_camaras/LICENSE")
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".png", ".svg", ".yaml"}:
            archive.write(path, path.relative_to(root))
print(target)

"""Finding the images in a chosen folder.

The app is meant to be pointed at a folder that contains nothing but plate
photos. Anything that isn't an image is silently ignored; images in a format the
app can't display are collected separately so the UI can warn about them instead
of quietly dropping plates from the count.
"""

from pathlib import Path

# What we can display and run inference on.
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}

# Image formats we recognise but deliberately don't handle. Skipping one of
# these silently would mean a plate never gets counted, so we warn.
OTHER_IMAGE_EXTS = {
    ".tif", ".tiff", ".bmp", ".dib", ".gif", ".webp", ".heic", ".heif",
    ".jp2", ".j2k", ".jpf", ".jpx", ".pbm", ".pgm", ".ppm", ".pnm",
    ".ras", ".sr", ".exr", ".hdr", ".ico", ".psd", ".dng", ".cr2", ".nef",
    ".arw", ".svg", ".avif",
}


class FolderScan:
    """Result of looking at one folder."""

    def __init__(self, folder, images, unsupported_images, ignored):
        self.folder = folder
        self.images = images                      # list[Path], usable
        self.unsupported_images = unsupported_images  # list[Path], warn about
        self.ignored = ignored                    # list[Path], non-image clutter

    @property
    def warning(self):
        """Human-readable warning about unusable images, or None."""
        if not self.unsupported_images:
            return None
        by_ext = {}
        for p in self.unsupported_images:
            by_ext.setdefault(p.suffix.lower(), []).append(p.name)
        parts = [
            f"{ext} ({len(names)} file{'s' if len(names) != 1 else ''})"
            for ext, names in sorted(by_ext.items())
        ]
        examples = ", ".join(sorted(n for p in self.unsupported_images[:4] for n in [p.name]))
        return (
            f"{len(self.unsupported_images)} image(s) in this folder are in a format "
            f"this app cannot open, and will be skipped:\n\n"
            f"    {', '.join(parts)}\n\n"
            f"For example: {examples}\n\n"
            f"Only .jpg, .jpeg and .png are supported. Convert these files to "
            f"JPEG or PNG if you need them counted."
        )


def _is_hidden(path):
    """macOS litter: .DS_Store, AppleDouble ._ sidecars, dotfiles in general."""
    return path.name.startswith(".") or path.name.startswith("._")


def scan_folder(folder):
    """Sort the entries of `folder` into usable images, bad-format images, clutter.

    Non-recursive: only files directly inside the folder are considered.
    """
    folder = Path(folder)
    images, unsupported, ignored = [], [], []

    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir() or _is_hidden(entry):
            continue
        ext = entry.suffix.lower()
        if ext in SUPPORTED_EXTS:
            images.append(entry)
        elif ext in OTHER_IMAGE_EXTS:
            unsupported.append(entry)
        else:
            ignored.append(entry)

    return FolderScan(folder, images, unsupported, ignored)

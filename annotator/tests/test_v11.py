"""Leaner project files (v1.6.3).

Run with:  ../.venv/bin/python tests/test_v11.py     (from annotator/)

Three changes, all invisible when they work:

  * `save` no longer indents. Half a large project file used to be spaces and
    newlines, for formatting nobody reads.
  * `save` gzips what is left, since JSON this repetitive compresses better
    than three to one.
  * `load` adopts boxes that are already in shape instead of rebuilding every
    one of them, which was most of the time spent opening a big project.

None of it may change a single stored number. Compression is deliberately
one-way — a project written now will not open in an older build — so the tests
lean hardest on the direction that must keep working: every plain-JSON file
older versions wrote still has to load. A file damaged in transit must say so
rather than quietly losing colonies.
"""

import gzip
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="cfu_v11_"))

from cfu_annotator import project                             # noqa: E402

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)


def boxes(n, start=0):
    return [{"cls": i % 3, "conf": round(0.5 + i / 1000, 4),
             "xyxy": [10.0 + i, 20.5 + i, 40.25 + i, 60.75 + i]}
            for i in range(start, start + n)]


def records(n_images, per_image):
    return {f"plate{i}.jpg": {"boxes": boxes(per_image),
                              "annotated": True, "edited": bool(i % 2),
                              "finalized": False, "contaminated": False,
                              "model": "best.pt",
                              "params": {"conf": 0.25, "tiling": True}}
            for i in range(n_images)}


STATE = dict(
    image_folder="/data/plates", model_path="/models/best.pt",
    output_folder="/data/out", export_options={"csv": True, "areas": True},
    detection={"conf": 0.25, "tiling": True},
    class_names=["BFU", "GM", "E", "GEMM"],
    custom_classes=False, class_list_source=None,
)


def try_load(path):
    """Load, turning a failure into a value the checks can report on.

    Opening files older versions wrote is the one guarantee compression must
    not cost, so a regression there should read as a named failure rather than
    a traceback that stops the rest of the file from running.
    """
    try:
        return project.load(path), None
    except Exception as exc:                                   # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def payload_of(path):
    """The JSON inside a saved project, whether or not it is compressed."""
    raw = Path(path).read_bytes()
    if raw[:2] == project.GZIP_MAGIC:
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def save(path, recs, sizes=None):
    return project.save(path, records=recs,
                        image_sizes=sizes if sizes is not None
                        else {n: (6400, 6400) for n in recs},
                        **STATE)


print("\n-- what a saved file now looks like --")
recs = records(8, 200)
p = save(TMP / "a.cfuproj", recs)
raw = p.read_bytes()
check("it is gzipped", raw[:2] == project.GZIP_MAGIC, str(raw[:2]))
check("no leftover .tmp file",
      not (TMP / "a.cfuproj.tmp").exists() and p.suffix == project.EXTENSION)

inner = gzip.decompress(raw)
check("what is inside is ordinary JSON",
      isinstance(json.loads(inner.decode()), dict))
check("and it is compact: no newlines at all", b"\n" not in inner,
      f"{inner.count(bytes([10]))} newline(s) found")
check("no runs of indent spaces", b"  " not in inner)
check("no space after a colon", b'": ' not in inner)

payload = json.loads(inner.decode())
old_style = json.dumps(payload, indent=1).encode()
check("compact is about half the size of the indented form",
      len(inner) < len(old_style) * 0.6,
      f"{len(inner)/1e3:.0f} kB vs {len(old_style)/1e3:.0f} kB "
      f"({len(inner)/len(old_style)*100:.0f}%)")
check("compressing it wins at least another 2x",
      len(raw) < len(inner) / 2,
      f"{len(raw)/1e3:.0f} kB vs {len(inner)/1e3:.0f} kB "
      f"({len(inner)/len(raw):.1f}x)")
check("all told, a fraction of what the old format cost",
      len(raw) < len(old_style) / 4,
      f"{len(raw)/1e3:.0f} kB vs {len(old_style)/1e3:.0f} kB "
      f"({len(old_style)/len(raw):.1f}x smaller)")

print("\n-- nothing about the contents changed --")
loaded = project.load(p)
check("every image comes back", set(loaded["records"]) == set(recs))
for name in recs:
    a, b = recs[name], loaded["records"][name]
    if a["boxes"] != b["boxes"] or a["annotated"] != b["annotated"]:
        check(f"{name} round-trips unchanged", False, "boxes or flags differ")
        break
else:
    check("every box and flag round-trips unchanged", True)
check("image sizes round-trip as int tuples",
      loaded["image_sizes"]["plate0.jpg"] == (6400, 6400),
      str(loaded["image_sizes"]["plate0.jpg"]))
check("class names round-trip", loaded["class_names"] == STATE["class_names"])
check("export options round-trip",
      loaded["export_options"] == STATE["export_options"])
check("per-image params round-trip",
      loaded["records"]["plate0.jpg"]["params"] == {"conf": 0.25, "tiling": True})

print("\n-- every project an older version could have written still opens --")
# Uncompressed and indented, as everything before v1.6.3 was written.
old_path = TMP / "old.cfuproj"
old_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
old_loaded, why = try_load(old_path)
check("a plain, indented file loads", old_loaded is not None, why or "")
check("it holds every image",
      old_loaded and set(old_loaded["records"]) == set(loaded["records"]))
check("and gives byte-identical boxes",
      old_loaded and old_loaded["records"]["plate3.jpg"]["boxes"]
      == loaded["records"]["plate3.jpg"]["boxes"])

# Uncompressed and compact, in case one was written between the two changes.
mid_path = TMP / "mid.cfuproj"
mid_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
mid_loaded, why = try_load(mid_path)
check("a plain, compact file loads", mid_loaded is not None, why or "")
check("with the same boxes",
      mid_loaded and mid_loaded["records"]["plate3.jpg"]["boxes"]
      == loaded["records"]["plate3.jpg"]["boxes"])
check("an old file's flags and sizes survive too",
      old_loaded and old_loaded["image_sizes"] == loaded["image_sizes"]
      and old_loaded["records"]["plate1.jpg"]["edited"]
      == loaded["records"]["plate1.jpg"]["edited"])

# Re-saving an old project quietly upgrades it to the compressed form.
if old_loaded:
    project.save(old_path, records=old_loaded["records"],
                 image_sizes=old_loaded["image_sizes"], **STATE)
    again, why = try_load(old_path)
    check("re-saving an old project compresses it",
          old_path.read_bytes()[:2] == project.GZIP_MAGIC)
    check("and it still reads back the same",
          again and again["records"]["plate3.jpg"]["boxes"]
          == loaded["records"]["plate3.jpg"]["boxes"], why or "")

print("\n-- adopting is not the same as skipping the check --")
# An already-clean box is taken as it stands: an unknown key survives, which is
# only possible if the dict was adopted rather than rebuilt.
hand = {"format": project.FORMAT, "version": 1, "class_names": ["a"],
        "images": {"p.jpg": {"boxes": [
            {"cls": 1, "conf": 0.9, "xyxy": [1.0, 2.0, 3.0, 4.0], "note": "kept"}
        ], "annotated": True}}}
hp = TMP / "hand.cfuproj"
hp.write_text(json.dumps(hand), encoding="utf-8")
got = project.load(hp)["records"]["p.jpg"]["boxes"][0]
check("a clean box is adopted, not rebuilt", got.get("note") == "kept", str(got))
check("its values are untouched",
      (got["cls"], got["conf"], got["xyxy"]) == (1, 0.9, [1.0, 2.0, 3.0, 4.0]),
      str(got))

print("\n-- a box that is not already clean is repaired --")
messy = {"format": project.FORMAT, "version": 1, "class_names": ["a"],
         "images": {"p.jpg": {"boxes": [
             {"cls": "2", "conf": "0.87654321", "xyxy": ["1.005", 2, 3, "4.9"]},
             {"cls": 1.0, "conf": None, "xyxy": [1, 2, 3, 4]},
             {"cls": 0, "conf": 0.5, "xyxy": [1.0, 2.0, 3.0, 4.0],
              "unconfirmed": True},
         ], "annotated": True}}}
mp = TMP / "messy.cfuproj"
mp.write_text(json.dumps(messy), encoding="utf-8")
out = project.load(mp)["records"]["p.jpg"]["boxes"]
check("a string class is coerced to int", out[0]["cls"] == 2 and type(out[0]["cls"]) is int)
check("a string coordinate is coerced and rounded",
      out[0]["xyxy"] == [1.0, 2.0, 3.0, 4.9], str(out[0]["xyxy"]))
check("confidence is rounded to 4 places", out[0]["conf"] == 0.8765, str(out[0]["conf"]))
check("a float class is coerced", out[1]["cls"] == 1 and type(out[1]["cls"]) is int)
check("a null confidence stays null", out[1]["conf"] is None)
check("an unconfirmed flag survives", out[2].get("unconfirmed") is True)

print("\n-- a class of `true` does not slip through as 1 --")
sneaky = {"format": project.FORMAT, "version": 1, "class_names": ["a"],
          "images": {"p.jpg": {"boxes": [
              {"cls": True, "conf": 0.5, "xyxy": [1.0, 2.0, 3.0, 4.0]}
          ], "annotated": True}}}
sp = TMP / "sneaky.cfuproj"
sp.write_text(json.dumps(sneaky), encoding="utf-8")
got = project.load(sp)["records"]["p.jpg"]["boxes"][0]
check("it takes the repair path and comes out a real int",
      type(got["cls"]) is int and got["cls"] == 1, f"{got['cls']!r}")

print("\n-- an unreadable box is an error, not a silent loss --")
for label, bad in (
    ("three coordinates", {"cls": 0, "conf": 0.5, "xyxy": [1.0, 2.0, 3.0]}),
    ("no coordinates at all", {"cls": 0, "conf": 0.5}),
    ("coordinates that are not numbers",
     {"cls": 0, "conf": 0.5, "xyxy": ["a", "b", "c", "d"]}),
    ("a box that is not an object", "not a box"),
):
    broken = {"format": project.FORMAT, "version": 1, "class_names": ["a"],
              "images": {"plate9.jpg": {"boxes": [
                  {"cls": 0, "conf": 0.5, "xyxy": [1.0, 2.0, 3.0, 4.0]}, bad
              ], "annotated": True}}}
    bp = TMP / "broken.cfuproj"
    bp.write_text(json.dumps(broken), encoding="utf-8")
    try:
        project.load(bp)
        check(f"{label} is rejected", False, "loaded without complaint")
    except project.ProjectError as exc:
        message = str(exc)
        check(f"{label} is rejected",
              "plate9.jpg" in message and "2" in message, message[:90])
    except Exception as exc:                                   # noqa: BLE001
        check(f"{label} is rejected", False,
              f"raised {type(exc).__name__}, not ProjectError")

print("\n-- a damaged compressed file says so --")
good = (TMP / "a.cfuproj").read_bytes()
for label, payload_bytes in (
    ("a truncated file", good[:len(good) // 2]),
    ("a gzip header over junk", project.GZIP_MAGIC + b"\x08" + b"\x00" * 40),
):
    dp = TMP / "damaged.cfuproj"
    dp.write_bytes(payload_bytes)
    try:
        project.load(dp)
        check(f"{label} is refused", False, "loaded without complaint")
    except project.ProjectError as exc:
        check(f"{label} is refused",
              "truncated or damaged" in str(exc) or "not a valid project" in str(exc),
              str(exc)[:80])
    except Exception as exc:                                   # noqa: BLE001
        check(f"{label} is refused", False,
              f"raised {type(exc).__name__}, not ProjectError")

# bytes that are not text at all, uncompressed
bp = TMP / "binary.cfuproj"
bp.write_bytes(b"\xff\xfe\x00\x01 not text")
try:
    project.load(bp)
    check("a file that is not text is refused", False, "loaded without complaint")
except project.ProjectError:
    check("a file that is not text is refused", True)
except Exception as exc:                                       # noqa: BLE001
    check("a file that is not text is refused", False,
          f"raised {type(exc).__name__}, not ProjectError")

print("\n-- junk files are still refused the way they were --")
for label, text in (("not JSON", "{{{"),
                    ("JSON but not a project", '{"hello": 1}')):
    jp = TMP / "junk.cfuproj"
    jp.write_text(text, encoding="utf-8")
    try:
        project.load(jp)
        check(f"{label} is refused", False, "loaded without complaint")
    except project.ProjectError:
        check(f"{label} is refused", True)
jp.write_text(json.dumps({"format": project.FORMAT, "version": 99}), encoding="utf-8")
try:
    project.load(jp)
    check("a newer project version is refused", False)
except project.ProjectError as exc:
    check("a newer project version is refused", "newer version" in str(exc))

print("\n-- coordinates are still stored to 0.01 px --")
fine = {"p.jpg": {"boxes": [{"cls": 0, "conf": 0.123456789,
                             "xyxy": [1.23456, 2.99999, 3.5, 4.0]}],
                  "annotated": True, "edited": False,
                  "finalized": False, "contaminated": False}}
fp = save(TMP / "fine.cfuproj", fine)
stored = payload_of(fp)["images"]["p.jpg"]["boxes"][0]
check("saved coordinates keep two decimals",
      stored["xyxy"] == [1.23, 3.0, 3.5, 4.0], str(stored["xyxy"]))
check("saved confidence keeps four", stored["conf"] == 0.1235, str(stored["conf"]))
check("and they survive the trip home",
      project.load(fp)["records"]["p.jpg"]["boxes"][0]["xyxy"]
      == [1.23, 3.0, 3.5, 4.0])

print("\n-- an empty project and empty plates still work --")
ep = save(TMP / "empty.cfuproj", {})
check("a project with no images saves and loads",
      project.load(ep)["records"] == {})
np_ = save(TMP / "noboxes.cfuproj",
           {"p.jpg": {"boxes": [], "annotated": False, "edited": False,
                      "finalized": False, "contaminated": False}})
check("a plate with no boxes round-trips",
      project.load(np_)["records"]["p.jpg"]["boxes"] == [])

print("\n-- the saving is still atomic --")
target = TMP / "atomic.cfuproj"
save(target, records(2, 5))
before = target.read_bytes()
try:
    save(target, {"p.jpg": {"boxes": [{"cls": None, "conf": 0.5,
                                       "xyxy": [1, 2, 3, 4]}],
                            "annotated": True}})
except Exception:                                              # noqa: BLE001
    pass
check("a failed save leaves the old file intact", target.read_bytes() == before)
check("and leaves no .tmp behind",
      not target.with_suffix(target.suffix + ".tmp").exists())

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.stdout.flush()
sys.exit(1 if fails else 0)

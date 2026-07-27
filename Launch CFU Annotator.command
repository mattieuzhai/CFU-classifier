#!/bin/bash
# Double-click this file in Finder to start the CFU Annotator.
#
# It finds the project's Python environment, checks that everything the app
# needs is installed (offering to install anything missing), and launches the app.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1

echo "CFU Annotator"
echo "============="
echo

# --- 1. Find a Python interpreter -------------------------------------------
PY=""
for candidate in "$HERE/.venv/bin/python" "$HERE/.venv/bin/python3" "$(command -v python3 || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "ERROR: No Python installation was found."
    echo
    echo "Install Python 3 from https://www.python.org/downloads/ and then"
    echo "double-click this file again."
    echo
    read -r -p "Press Return to close this window. " _
    exit 1
fi

echo "Using Python: $PY"
"$PY" --version
echo

# --- 2. Check the packages the app needs ------------------------------------
MISSING="$("$PY" - <<'PYCODE'
needed = {"PyQt5": "PyQt5", "numpy": "numpy", "PIL": "pillow", "ultralytics": "ultralytics"}
missing = []
for module, package in needed.items():
    try:
        __import__(module)
    except ImportError:
        missing.append(package)
print(" ".join(missing))
PYCODE
)"

if [ -n "$MISSING" ]; then
    echo "These Python packages are missing: $MISSING"
    echo
    echo "They can be installed automatically (this needs an internet connection"
    echo "and may take a few minutes — ultralytics/torch are large)."
    echo
    read -r -p "Install them now? [y/N] " REPLY
    case "$REPLY" in
        [Yy]*)
            echo
            # shellcheck disable=SC2086
            "$PY" -m pip install --upgrade pip
            # shellcheck disable=SC2086
            "$PY" -m pip install $MISSING || {
                echo
                echo "ERROR: installation failed. See the messages above."
                read -r -p "Press Return to close this window. " _
                exit 1
            }
            echo
            ;;
        *)
            echo
            echo "Cannot start without those packages. To install them yourself, run:"
            echo
            echo "    $PY -m pip install $MISSING"
            echo
            read -r -p "Press Return to close this window. " _
            exit 1
            ;;
    esac
fi

# --- 3. Launch ---------------------------------------------------------------
echo "Starting the app — this window can be minimised, but leave it open."
echo
"$PY" -m app
STATUS=$?

echo
# Codes above 128 mean the app was terminated by a signal (e.g. Force Quit),
# which isn't a crash worth alarming anyone about.
if [ $STATUS -ne 0 ] && [ $STATUS -lt 128 ]; then
    echo "The app exited with an error (code $STATUS). The messages above may explain why."
    read -r -p "Press Return to close this window. " _
else
    echo "CFU Annotator closed."
fi
exit $STATUS

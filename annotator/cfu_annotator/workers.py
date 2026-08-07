"""Background threads, so loading a model or running inference never freezes the UI."""

from PyQt5.QtCore import QThread, pyqtSignal

from . import render
from .detector import Detector


class ModelLoadWorker(QThread):
    """Imports ultralytics/torch and loads the .pt file off the UI thread."""

    loaded = pyqtSignal(object)      # Detector
    failed = pyqtSignal(str)

    def __init__(self, weights_path, parent=None):
        super().__init__(parent)
        self.weights_path = weights_path

    def run(self):
        try:
            self.loaded.emit(Detector(self.weights_path))
        except Exception as exc:
            self.failed.emit(str(exc))


class InferenceWorker(QThread):
    """Runs detection over one or more images, reporting progress per tile."""

    image_started = pyqtSignal(str, int, int)     # name, index (1-based), total
    tile_progress = pyqtSignal(int, int)          # tiles done, tiles total
    image_done = pyqtSignal(str, object)          # name, list of detections
    image_failed = pyqtSignal(str, str)           # name, error
    all_done = pyqtSignal(bool)                   # True if cancelled

    def __init__(self, detector, image_paths, settings, parent=None):
        super().__init__(parent)
        self.detector = detector
        self.image_paths = list(image_paths)
        self.settings = dict(settings)
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        total = len(self.image_paths)
        for index, path in enumerate(self.image_paths, start=1):
            if self._stop:
                break
            self.image_started.emit(path.name, index, total)
            try:
                detections = self.detector.predict(
                    path,
                    progress=lambda done, tiles: self.tile_progress.emit(done, tiles),
                    should_stop=lambda: self._stop,
                    **self.settings,
                )
            except Exception as exc:
                self.image_failed.emit(path.name, str(exc))
                continue
            if detections is None:      # cancelled mid-image
                break
            self.image_done.emit(path.name, detections)
        self.all_done.emit(self._stop)


class ImageExportWorker(QThread):
    """Draws boxes onto copies of the plates and saves them, off the UI thread.

    Full-resolution plates take a second or two each to render, so doing this
    inline would freeze the window for a minute on a full experiment.
    """

    progress = pyqtSignal(int, int, str)   # done, total, current filename
    finished_all = pyqtSignal(int, list)   # written count, list of error strings

    def __init__(self, jobs, class_names, show_confidence=False, parent=None):
        super().__init__(parent)
        # jobs: [(source Path, destination Path, boxes), ...]
        self.jobs = list(jobs)
        self.class_names = list(class_names)
        self.show_confidence = show_confidence
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        written, errors = 0, []
        total = len(self.jobs)
        for index, (source, dest, boxes) in enumerate(self.jobs, start=1):
            if self._stop:
                break
            self.progress.emit(index, total, source.name)
            try:
                problem = render.render(
                    source, boxes, self.class_names, dest,
                    show_confidence=self.show_confidence,
                )
            except Exception as exc:                       # noqa: BLE001
                problem = f"{source.name}: {type(exc).__name__}: {exc}"
            if problem:
                errors.append(problem)
            else:
                written += 1
        self.finished_all.emit(written, errors)

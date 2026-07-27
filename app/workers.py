"""Background threads, so loading a model or running inference never freezes the UI."""

from PyQt5.QtCore import QThread, pyqtSignal

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

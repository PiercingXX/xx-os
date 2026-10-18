"""Overlay first run: model download in progress (state only)."""
win._downloading = True
win._progress.set_visible(True)
win._progress.set_fraction(0.42)
win._cancel.set_visible(True)
win._download.set_visible(False)

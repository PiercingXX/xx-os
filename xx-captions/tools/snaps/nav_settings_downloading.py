"""Settings: the model card mid-download (no network: state only)."""
from xx_captions import config

win._settings["model"] = config.VOSK_ID
win._downloading = True
win._refresh_model_card()
win._model_progress.set_fraction(0.42)

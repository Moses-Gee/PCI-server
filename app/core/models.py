import os
import logging

logger = logging.getLogger(__name__)

BBOX_MODEL_PATH = os.path.join(os.getcwd(), "app", "vision_models", "bbox_model.pt")
BBOX_MODEL_PATH_EXISTS = os.path.exists(BBOX_MODEL_PATH)
BBOX_MODEL = None

SEG_MODEL_PATH = os.path.join(os.getcwd(), "app", "vision_models", "seg_model.pt")
SEG_MODEL_PATH_EXISTS = os.path.exists(SEG_MODEL_PATH)
SEG_MODEL = None


def load_models():
    global BBOX_MODEL
    global SEG_MODEL

    if not BBOX_MODEL_PATH_EXISTS:
        logger.error(f"BBOX Model file not found at {BBOX_MODEL_PATH}")
        raise FileNotFoundError(f"YOLO BBOX model not found: {BBOX_MODEL_PATH}")

    from ultralytics import YOLO

    BBOX_MODEL = YOLO(BBOX_MODEL_PATH)
    # BBOX_MODEL = None
    logger.info(f"Loaded BBOX model from {BBOX_MODEL_PATH}")

    if not SEG_MODEL_PATH_EXISTS:
        logger.error(f"SEG Model file not found at {SEG_MODEL_PATH}")
        raise FileNotFoundError(f"YOLO SEG model not found: {SEG_MODEL_PATH}")

    SEG_MODEL = YOLO(SEG_MODEL_PATH)
    logger.info(f"Loaded SEG model from {SEG_MODEL_PATH}")

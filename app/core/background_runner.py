import threading
import logging

logger = logging.getLogger(__name__)


def run_in_background(fn, *args, **kwargs):
    """
    Fire-and-forget: runs fn(*args, **kwargs) in a daemon thread.
    Equivalent to celery_task.delay(*args, **kwargs).
    """
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
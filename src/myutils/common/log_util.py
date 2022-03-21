import logging
import sys

from . import file_util


def setup_logging(log_file_path):
    file_util.make_parent_dirs(log_file_path)
    log_format = '%(asctime)-15s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(log_format)
    # Get root level logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    # Stream INFO level logging events to console
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    root_logger.addHandler(sh)
    # If enabled write DEBUG level logging events to log file
    if log_file_path is not None:
        fh = logging.FileHandler(log_file_path, mode='w')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        root_logger.addHandler(fh)
        logging.info('DEBUG level logging enabled. Log written to: `{}`.'.format(log_file_path))

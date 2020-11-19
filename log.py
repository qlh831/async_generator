import logging


logging.basicConfig(level=logging.DEBUG, format="[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d] %(message)s")
app_log = logging.getLogger("qlh")

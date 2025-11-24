import baseball_pipe.web_server
import os, sys, logging

APP = "baseball_pipe"

# CONFIGURE LOGGING
appdata_local = os.getenv("LOCALAPPDATA")
log_file_path = os.path.join(appdata_local, APP, f"{APP}.log")
os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)
file_handler = logging.FileHandler(filename=log_file_path)

logging.basicConfig(handlers=[stdout_handler, file_handler], 
                    encoding='utf-8',
                    format='%(levelname)s:%(message)s',
                    level=logging.DEBUG)

logger = logging.getLogger(__name__)
logger.info(f"{APP} started")

def main():
    ws = baseball_pipe.web_server.WebServer()
    ws.start()

if __name__ == '__main__':
    main()
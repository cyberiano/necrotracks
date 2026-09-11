"""python -m web — en la Pi por systemd (puerto 80); en la Mac: NECROTRACKS_WEB_PORT=8000."""
import os

import uvicorn


def main():
    port = int(os.environ.get("NECROTRACKS_WEB_PORT", "80"))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()

"""Send one test alert to the NTFY_TOPIC phone subscription."""
import os

import requests

from .notify import APP_URL


def main():
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC secret is not set")
        raise SystemExit(1)
    r = requests.post(f"https://ntfy.sh/{topic}", timeout=20,
                      data="Alerts are working. You'll get one like this for every new EV play.".encode(),
                      headers={"Title": "xBETS test alert", "Click": APP_URL, "Tags": "white_check_mark", "Priority": "high"})
    print("ntfy:", r.status_code)
    r.raise_for_status()


if __name__ == "__main__":
    main()

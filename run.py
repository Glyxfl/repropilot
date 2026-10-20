import os
import uvicorn
from repropilot import config  # load local .env before reading the port

if __name__ == "__main__":
    print("\n  ReproPilot · http://127.0.0.1:" + os.environ.get("REPROPILOT_PORT", "8765") + "\n")
    uvicorn.run("repropilot.server:app", host="127.0.0.1", port=int(os.environ.get("REPROPILOT_PORT", "8765")))

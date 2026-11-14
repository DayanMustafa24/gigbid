import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("gigsim_local.app:create_app", factory=True, host=os.environ.get("GIGSIM_HOST", "127.0.0.1"),
                port=int(os.environ.get("GIGSIM_PORT", "8081")))

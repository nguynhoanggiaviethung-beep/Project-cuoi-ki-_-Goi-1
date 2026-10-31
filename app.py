import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.sli.api:app",
        host=os.getenv("SLI_HOST", "127.0.0.1"),
        port=int(os.getenv("SLI_PORT", "8000")),
        reload=False,
    )
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Mounted under /api because the ALB ingress forwards the path unmodified
# (unlike the old nginx ingress, ALB has no rewrite-target equivalent).
router = APIRouter(prefix="/api")


@router.get("/hello")
def hello():
    return "hello"


@router.get("/world")
def world():
    return "world"


@router.get("/random")
def random():
    import random
    return random.randint(1, 100)


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

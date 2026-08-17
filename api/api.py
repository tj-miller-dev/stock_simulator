from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

# The docs endpoints hang off the app, not off the router below, so the router's
# /api prefix does not move them. Left at their defaults they sit at /docs and
# /openapi.json, which the ALB routes to the frontend -- so they have to be
# prefixed by hand. openapi_url matters as much as docs_url: the Swagger page
# fetches the schema from it, and pointing it at the frontend renders an empty
# "Failed to load API definition" page.
app = FastAPI(
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    # Unused today (no OAuth2 on this API) but it defaults to /docs/oauth2-redirect,
    # which would land on the frontend the moment auth is ever added.
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
)

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


@router.get("/bigrandom")
def bigrandom():
    import random
    return random.randint(1, 1000)

@router.get("/somethingspecial")
def somethingspecial():
    return "you are special"


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

"""Provider-mimicry surfaces, one module per provider.

Each module mounts at /api/v1/<name> and exposes:
- `router`: the FastAPI routes replicating that provider's wire format;
- `INDEX_ENTRY`: its section of the machine-readable /api index.

The rule every module must follow: everything after the provider segment is
that provider's own path/param/response grammar, byte-faithful enough that
the provider's SDKs and tutorials work with only a base-URL swap. Nothing
CuckooTrade-specific may leak into wire-compat responses beyond the
seed/generation extension params.
"""

from . import alpaca, alphavantage, polygon

PROVIDERS = {
    "alpaca": alpaca,
    "alphavantage": alphavantage,
    "polygon": polygon,
}

ROUTERS = [module.router for module in PROVIDERS.values()]
INDEX = {name: module.INDEX_ENTRY for name, module in PROVIDERS.items()}


def fault_renderer(path: str):
    """The error renderer belonging to whichever provider owns `path`.

    An injected fault has to look like that provider's own failures or it
    tests the wrong parser. Native paths get None; the caller falls back to
    CuckooTrade's own shape.
    """
    for name, module in PROVIDERS.items():
        if path.startswith(f"/api/v1/{name}/"):
            return module.fault_response
    return None

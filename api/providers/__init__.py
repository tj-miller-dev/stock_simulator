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

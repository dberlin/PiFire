from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ContractBundle:
    name: str
    models: tuple[type[BaseModel], ...]
    typescript_output: str


# Keep this explicit and sorted by bundle name. Contract additions must be
# reviewable registrations rather than side effects of module discovery.
WEB_CONTRACT_BUNDLES: tuple[ContractBundle, ...] = ()
